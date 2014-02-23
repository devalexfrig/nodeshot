import sys
import string
import random

from netaddr import ip

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.core.exceptions import ImproperlyConfigured
from django.contrib.gis.geos import Point
from django.utils.text import slugify
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
User = get_user_model()

if 'emailconfirmation' in settings.INSTALLED_APPS:
    EMAIL_ADDRESS_APP_INSTALLED = True
    from emailconfirmation.models import EmailAddress
else:
    EMAIL_ADDRESS_APP_INSTALLED = False

if 'nodeshot.core.layers' in settings.INSTALLED_APPS:
    from nodeshot.core.layers.models import Layer
    LAYER_APP_INSTALLED = True
else:
    LAYER_APP_INSTALLED = False

from nodeshot.core.base.utils import pause_disconnectable_signals, resume_disconnectable_signals
from nodeshot.core.nodes.models import Node, Status
from nodeshot.networking.net.models import *
from nodeshot.networking.net.models.choices import INTERFACE_TYPES
from nodeshot.networking.links.models import Link
from nodeshot.networking.links.models.choices import LINK_STATUS, LINK_TYPES, METRIC_TYPES
from nodeshot.community.mailing.models import Inward
from nodeshot.extra.oldimporter.models import *


class Command(BaseCommand):
    """
    Will try to import data from old nodeshot.
    
    Requirements for settings:
        * nodeshot.extra.oldimporter must be in INSTALLED_APPS
        * old_nodeshot database must be configured
        * database routers directives must be uncommented
    
    Steps:
    
    1.  Retrieve all nodes
        Retrieve all nodes from old db and convert queryset in a python list.
    
    2.  Extract user data from nodes
        
        (Since in old nodeshot there are no users but each node contains data
        such as name, email, and stuff like that)
            
            * loop over nodes and extract a list of unique emails
            * each unique email will be a new user in the new database
            * each new user will have a random password set
            * save users, email addresses
    
    3.  Import nodes
        
            * USER: assign owner (the link is the email)
            * LAYER: assign layer (layers must be created by hand first!):
                1. if node has coordinates comprised in a specified layer choose that
                2. if node has coordinates comprised in more than one layer prompt the user which one to choose
                3. if node does not have coordinates comprised in any layer:
                    1. use default layer if specified (configured in settings)
                    2. discard the node if no default layer specified
            * STATUS: assign status depending on configuration:
                settings.NODESHOT['OLD_IMPORTER']['STATUS_MAPPING'] must be a dictionary in which the
                key is the old status value while the value is the new status value
                if settings.NODESHOT['OLD_IMPORTER']['STATUS_MAPPING'] is False the default status will be used
            * HOSTPOT: if status is hotspot or active and hotspot add this info in HSTORE data field
            
    4.  Import devices
        Create any missing routing protocol
    
    5.  Import interfaces, ip addresses, vaps
    
    6.  Import links
    
    7.  Import Contacts
    
    TODO: Decide what to do with statistics and hna.
    """
    help = 'Import old nodeshot data. Layers and Status must be created first.'
    
    status_mapping = settings.NODESHOT['OLD_IMPORTER'].get('STATUS_MAPPING', False)
    # if no default layer some nodes might be discarded
    default_layer = settings.NODESHOT['OLD_IMPORTER'].get('DEFAULT_LAYER', False)
    
    old_nodes = []
    saved_users = []
    saved_nodes = []
    saved_devices = []
    routing_protocols_added = []
    saved_interfaces = []
    saved_vaps = []
    saved_ipv4 = []
    saved_ipv6 = []
    saved_links = []
    saved_contacts = []
    
    def message(self, message):
        self.stdout.write('%s\n\r' % message) 
    
    def verbose(self, message):
        if self.verbosity == 2:
            self.message(message)
    
    def handle(self, *args, **options):
        """ execute synchronize command """
        delete = False
        
        try:
            # blank line
            self.stdout.write('\r\n')
            # store verbosity level in instance attribute for later use
            self.verbosity = int(options.get('verbosity'))
            
            self.verbose('disabling signals (notififcations, websocket alerts)')
            pause_disconnectable_signals()
            
            self.check_status_mapping()
            self.retrieve_nodes()
            self.extract_users()
            self.import_users()
            self.import_nodes()
            self.import_devices()
            self.import_interfaces()
            self.import_links()
            self.import_contacts()
            
            self.confirm_operation_completed()
            
            resume_disconnectable_signals()
            self.verbose('re-enabling signals (notififcations, websocket alerts)')
            
        except KeyboardInterrupt:
            self.message('\n\nOperation cancelled...')
            delete = True
        except Exception as e:
            delete = True
            # rollback database transaction
            transaction.rollback()
            self.message('Got exception %s' % e)
        
        if delete:
            self.delete_imported_data()
    
    def confirm_operation_completed(self):
        self.message("Are you satisfied with the results? If not all imported data will be deleted\n\n[Y/n]")
        
        while True:
            answer = raw_input().lower()
            if answer == '':
                answer = "y"
            
            if answer in ['y', 'n']:
                break
            else:
                self.message("Please respond with one of the valid answers\n")
        
        if answer == 'n':
            self.delete_imported_data()
        else:
            self.message('Operation completed!')
    
    def delete_imported_data(self):
        self.message('Going to delete all the imported data...')
        
        for interface in self.saved_interfaces:
            try:
                interface.delete()
            except Exception as e:
                self.message('Got exception while deleting interface %s: %s' % (interface.mac, e))
        
        for device in self.saved_devices:
            try:
                device.delete()
            except Exception as e:
                self.message('Got exception while deleting device %s: %s' % (device.name, e))
        
        for routing_protocol in self.routing_protocols_added:
            try:
                routing_protocol.delete()
            except Exception as e:
                self.message('Got exception while deleting routing_protocol %s: %s' % (routing_protocol.name, e))
        
        for node in self.saved_nodes:
            try:
                node.delete()
            except Exception as e:
                self.message('Got exception while deleting node %s: %s' % (node.name, e))
        
        for contact in self.saved_contacts:
            try:
                contact.delete()
            except Exception as e:
                self.message('Got exception while deleting contact log entry %s: %s' % (contact.id, e))
        
        for user in self.saved_users:
            try:
                user.delete()
            except Exception as e:
                self.message('Got exception while deleting user %s: %s' % (user.username, e))
    
    def prompt_layer_selection(self, node, layers):
        """Ask user what to do when an old node is contained in more than one layer.
        Possible answers are:
            * use default layer (default answer if pressing enter)
            * choose layer
            * discard node
        """
        valid = {
            "default": "default",
            "def":     "default",
            "discard": "discard",
            "dis":     "discard",
        }
        question = """Cannot automatically determine layer for node "%s" because there \
are %d layers available in that area, what do you want to do?\n\n""" % (node.name, len(layers))
        
        available_layers = ""
        for layer in layers:
            available_layers += "%d (%s)\n" % (layer.id, layer.name)
            valid[str(layer.id)] = layer.id
        
        prompt = """\
choose (enter the number of) one of the following layers:
%s
"default"    use default layer (if no default layer specified in settings node will be discarded)
"discard"    discard node

(default action is to use default layer)\n\n""" % available_layers
        
        sys.stdout.write(question + prompt)
        
        while True:
            answer = raw_input().lower()
            if answer == '':
                answer = "default"
            
            if answer in valid:
                answer = valid[answer]
                break
            else:
                sys.stdout.write("Please respond with one of the valid answers\n")
        
        sys.stdout.write("\n")
        return answer
    
    @classmethod
    def generate_random_password(cls, size=10, chars=string.ascii_uppercase+string.digits):
        return ''.join(random.choice(chars) for x in range(size))

    def check_status_mapping(self):
        """ ensure status map does not contain status values which are not present in DB """
        self.verbose('checking status mapping...')
        
        if not self.status_mapping:
            self.message('no status mapping found')
            return
        
        for old_val, new_val in self.status_mapping.iteritems():
            try:
                status = Status.objects.get(slug=new_val)
                self.status_mapping[old_val] = status.id
            except Status.DoesNotExist:
                raise ImproperlyConfigured('Error! Status with slug %s not found in the database' % new_val)
        
        self.verbose('status map correct')
    
    def get_status(self, value):
        self.status_mapping.get(value, self.status_mapping['default'])

    def retrieve_nodes(self):
        """ retrieve nodes from old mysql DB """
        self.verbose('retrieving nodes from old mysql DB...')
        
        # tmp
        OldNode.objects.all()
        
        self.old_nodes = list(OldNode.objects.all())
        self.message('retrieved %d nodes' % len(self.old_nodes))
    
    def extract_users(self):
        """ extrac user info """
        email_set = set()
        users_dict = {}
        
        self.verbose('going to extract user information from retrieved nodes...')
        
        for node in self.old_nodes:
            email_set.add(node.email)
            
            if not users_dict.has_key(node.email):
                users_dict[node.email] = {
                    'owner': node.owner
                }
        
        self.email_set = email_set
        self.users_dict = users_dict
        
        self.verbose('%d users extracted' % len(email_set))
    
    def import_users(self):
        """ save users to local DB """
        self.message('saving users into local DB')
        
        saved_users = []
        
        # loop over all extracted unique email addresses
        for email in self.email_set:
            owner = self.users_dict[email].get('owner')
            
            # if owner is not specified, build username from email
            if owner.strip() == '':
                owner, domain = email.split('@')
                # replace any points with a space
                owner = owner.replace('.', ' ')
            
            # if owner has a space, assume he specified first and last name
            if ' ' in owner:
                owner_parts = owner.split(' ')
                first_name = owner_parts[0]
                last_name = owner_parts[1]
            else:
                first_name = owner
                last_name = ''
            
            # username must be slugified otherwise won't get into the DB
            username = slugify(owner)
            
            # create one user for each mail address
            user = User(**{
                "username": username,
                "password": self.generate_random_password(),
                "first_name": first_name.capitalize(),
                "last_name": last_name.capitalize(),
                "email": email,
                "is_active": True
            })
            
            # be sure username is unique
            counter = 1
            original_username = username
            while True:
                if User.objects.filter(username=user.username).count() > 0:
                    counter += 1
                    user.username = '%s%d' % (original_username, counter)
                else:
                    break
            
            try:
                # validate data and save
                user.full_clean()
                user.save()
                # store id
                self.users_dict[email]['id'] = user.id
                # append to saved users
                saved_users.append(user)
                self.verbose('Saved user %s (%s) with email <%s>' % (user.username, user.get_full_name(), user.email))
            except Exception as e:
                self.message('Could not save user %s, got exception: %s' % (user.username, e))
            
            # mark email address as confirmed if feature is enabled
            if EMAIL_ADDRESS_APP_INSTALLED:
                try:
                    email_address = EmailAddress(user=user, email=user.email, verified=True, primary=True)
                    email_address.full_clean()
                    email_address.save()
                except Exception as e:
                    self.message('Could not save email address for user %s, got exception: %s' % (user.username, e))
            
        self.message('saved %d users into local DB' % len(saved_users))
        self.saved_users = saved_users
    
    def import_nodes(self):
        """ import nodes into local DB """
        self.message('saving nodes into local DB...')
        
        saved_nodes = []
        
        # loop over all old node and create new nodes
        for old_node in self.old_nodes:
            # if this old node is unconfirmed skip to next cycle
            if old_node.status == 'u':
                continue
            
            node = Node(**{
                "id": old_node.id,
                "user_id": self.users_dict[old_node.email]['id'],
                "name": old_node.name,
                "slug": old_node.slug,
                "geometry": Point(old_node.lng, old_node.lat),
                "elev": old_node.alt,
                "description": old_node.description,
                "notes": old_node.notes,
                "added": old_node.added,
                "updated": old_node.updated,
                "data": {}
            })
            
            if LAYER_APP_INSTALLED:
                intersecting_layers = node.intersecting_layers
                # if more than one intersecting layer
                if len(intersecting_layers) > 1:
                    # prompt user
                    answer = self.prompt_layer_selection(node, intersecting_layers)
                    if isinstance(answer, int):
                        node.layer_id = answer
                    elif answer == 'default' and self.default_layer is not False:
                        node.layer_id = self.default_layer
                    else:
                        self.message('Node %s discarded' % node.name)
                        continue
                # if one intersecting layer select that
                elif 2 > len(intersecting_layers) > 0:
                    node.layer = intersecting_layers[0]
                # if no intersecting layers
                else:
                    if self.default_layer is False:
                        # discard node if no default layer specified
                        self.message("""Node %s discarded because is not contained
                                     in any specified layer and no default layer specified""" % node.name)
                        continue
                    else:
                        node.layer_id = self.default_layer
            
            if old_node.postal_code:
                # additional info
                node.data['postal_code'] = old_node.postal_code
            
            # is it a hotspot?
            if old_node.status in ['h', 'ah']:
                node.data['is_hotspot'] = 'true'
            
            # determine status according to settings
            if self.status_mapping:
                node.status_id = self.get_status(old_node.status)
            
            try:
                node.full_clean()
                node.save(auto_update=False)
                saved_nodes.append(node)
                self.verbose('Saved node %s in layer %s' % (node.name, node.layer))
            except Exception as e:
                self.message('Could not save node %s, got exception: %s' % (node.name, e))
        
        self.message('saved %d nodes into local DB' % len(saved_nodes))
        self.saved_nodes = saved_nodes
    
    def import_devices(self):
        self.verbose('retrieving devices from old mysql DB...')
        self.old_devices = list(OldDevice.objects.all())
        self.message('retrieved %d devices' % len(self.old_devices))
        
        saved_devices = []
        routing_protocols_added = []
        
        for old_device in self.old_devices:
            device = Device(**{
                "id": old_device.id,
                "node_id": old_device.node_id,
                "type": "radio",
                "name": old_device.name,
                "description": old_device.description,
                "added": old_device.added,
                "updated": old_device.updated,
                "data": {
                    "modello": old_device.type,
                    "cname": old_device.cname
                }
            })
            
            try:
                device.full_clean()
                device.save(auto_update=False)
                saved_devices.append(device)
                self.verbose('Saved device %s' % device.name)
            except Exception as e:
                self.message('Could not save device %s, got exception: %s' % (device.name, e))
            
            try:
                routing_protocol = RoutingProtocol.objects.filter(name__icontains=old_device.routing_protocol)[0]
            except IndexError:
                routing_protocol = RoutingProtocol.objects.create(name=old_device.routing_protocol)
                routing_protocols_added.append(routing_protocol)
            device.routing_protocols.add(routing_protocol)
            
        self.message('saved %d devices into local DB' % len(saved_devices))
        self.saved_devices = saved_devices
        self.routing_protocols_added = routing_protocols_added
    
    def import_interfaces(self):
        self.verbose('retrieving interfaces from old mysql DB...')
        self.old_interfaces = list(OldInterface.objects.all())
        self.message('retrieved %d interfaces' % len(self.old_interfaces))
        
        saved_interfaces = []
        saved_vaps = []
        saved_ipv4 = []
        saved_ipv6 = []
        
        for old_interface in self.old_interfaces:
            interface_dict = {
                "id": old_interface.id,
                "device_id": int(old_interface.device_id),
                "mac": old_interface.mac_address,
                "name": old_interface.cname[0:10],
                "added": old_interface.added,
                "updated": old_interface.updated,
                "data": {}
            }
            vap = None
            ipv4 = None
            ipv6 = None
            
            # determine interface type and specific fields
            if old_interface.type == 'eth':
                interface_dict['standard'] = 'fast'
                interface_dict['duplex'] = 'full'
                InterfaceModel = Ethernet
            elif old_interface.type == 'wifi':
                interface_dict['mode'] = old_interface.wireless_mode
                interface_dict['channel'] = old_interface.wireless_channel
                InterfaceModel = Wireless
                # determine ssid
                if old_interface.essid or old_interface.bssid:
                    vap = Vap(**{
                        "interface_id": old_interface.id,
                        "essid": old_interface.essid,
                        "bssid": old_interface.essid
                    })
                if old_interface.essid:
                    interface_dict['data']['essid'] = old_interface.essid
                if old_interface.bssid:
                    interface_dict['data']['bssid'] = old_interface.bssid
            elif old_interface.type == 'bridge':
                InterfaceModel = Bridge
            elif old_interface.type == 'vpn':
                InterfaceModel = Tunnel
            else:
                interface_dict['type'] = INTERFACE_TYPES.get('virtual')
                interface_dict['data']['old_nodeshot_interface_type'] = old_interface.get_type_display()
                InterfaceModel = Interface
            
            interface = InterfaceModel(**interface_dict)
            
            if old_interface.ipv4_address:
                old_interface.ipv4_address = old_interface.ipv4_address.strip()  # stupid django bug
                ipv4 = Ip(**{
                    "interface_id": old_interface.id,
                    "address": old_interface.ipv4_address
                })
                # ensure ipv4 is valid            
                try:
                    ip.IPAddress(old_interface.ipv4_address)
                except (ip.AddrFormatError, ValueError):
                    self.message('Invalid IPv4 address %s' % (old_interface.ipv4_address))
                    ipv4 = None
            
            if old_interface.ipv6_address:
                old_interface.ipv6_address = old_interface.ipv6_address.strip()  # stupid django bug
                ipv6 = Ip(**{
                    "interface_id": old_interface.id,
                    "address": old_interface.ipv6_address
                })
                # ensure ipv6 is valid            
                try:
                    ip.IPAddress(old_interface.ipv6_address)
                except (ip.AddrFormatError, ValueError):
                    self.message('Invalid IPv6 address %s' % (old_interface.ipv6_address))
                    ipv6 = None
            
            try:
                interface.full_clean()
                interface.save(auto_update=False)
                saved_interfaces.append(interface)
                self.verbose('Saved interface %s' % interface.name)
            except Exception as e:
                self.message('Could not save interface %s, got exception: %s' % (interface.mac, e))
                continue
            
            if vap:
                try:
                    vap.full_clean()
                    vap.save()
                    saved_vaps.append(vap)
                    self.verbose('Saved vap %s' % vap.essid or vap.bssid)
                except Exception as e:
                    self.message('Could not save vap %s, got exception: %s' % (vap.essid or vap.bssid, e))
            
            if ipv4:
                try:
                    ipv4.full_clean()
                    ipv4.save()
                    saved_ipv4.append(ipv4)
                    self.verbose('Saved ipv4 %s' % ipv4.address)
                except Exception as e:
                    self.message('Could not save ipv4 %s, got exception: %s' % (ipv4.address, e))
            
            if ipv6:
                try:
                    ipv6.full_clean()
                    ipv6.save()
                    saved_ipv6.append(ipv6)
                    self.verbose('Saved ipv6 %s' % ipv6.address)
                except Exception as e:
                    self.message('Could not save ipv6 %s, got exception: %s' % (ipv6.address, e))
            
        self.message('saved %d interfaces into local DB' % len(saved_interfaces))
        self.message('saved %d vaps into local DB' % len(saved_vaps))
        self.message('saved %d ipv4 addresses into local DB' % len(saved_ipv4))
        self.message('saved %d ipv6 addresses into local DB' % len(saved_ipv6))
        self.saved_interfaces = saved_interfaces
        self.saved_vaps = saved_vaps
        self.saved_ipv4 = saved_ipv4
        self.saved_ipv6 = saved_ipv6
    
    def import_links(self):
        self.verbose('retrieving links from old mysql DB...')
        self.old_links = list(OldLink.objects.all())
        self.message('retrieved %d links' % len(self.old_links))
        
        saved_links = []
        
        for old_link in self.old_links:
            
            skip = False
            
            try:
                interface_a = Interface.objects.get(pk=old_link.from_interface_id)
                if interface_a.type != INTERFACE_TYPES.get('wireless'):
                    interface_a.type = INTERFACE_TYPES.get('wireless')
                    interface_a.save()
            except Interface.DoesNotExist:
                self.message('Interface #%s does not exist, probably link #%s is orphan!' % (old_link.from_interface_id, old_link.id))
                skip = True
            
            try:
                interface_b = Interface.objects.get(pk=old_link.to_interface_id)
                if interface_b.type != INTERFACE_TYPES.get('wireless'):
                    interface_b.type = INTERFACE_TYPES.get('wireless')
                    interface_b.save()
            except Interface.DoesNotExist:
                self.message('Interface #%s does not exist, probably link #%s is orphan!' % (old_link.to_interface_id, old_link.id))
                skip = True
            
            if skip:
                self.verbose('Skipping to next cycle')
                continue
            
            old_bandwidth = [old_link.sync_tx, old_link.sync_rx]
            
            link = Link(**{
                "id": old_link.id,
                "interface_a": interface_a,
                "interface_b": interface_b,
                "status": LINK_STATUS.get('active'),
                "type": LINK_TYPES.get('radio'),
                "metric_type": 'etx',
                "metric_value": old_link.etx,
                "dbm": old_link.dbm,
                "min_rate": min(old_bandwidth),
                "max_rate": max(old_bandwidth),
            })
            if old_link.hide:
                link.access_level = 3
            
            try:
                link.full_clean()
                link.save()
                saved_links.append(link)
                self.verbose('Saved link %s' % link)
            except Exception as e:
                self.message('Could not save link %s, got exception: %s' % (old_link.id, e))
            
        self.message('saved %d links into local DB' % len(saved_links))
        self.saved_links = saved_links
    
    def import_contacts(self):
        self.verbose('retrieving contact log from old mysql DB...')
        self.old_contacts = list(OldContact.objects.all())
        self.message('retrieved %d entries from contact log' % len(self.old_contacts))
        
        saved_contacts = []
        
        content_type = ContentType.objects.only('id', 'model').get(app_label='nodes', model='node')
        
        for old_contact in self.old_contacts:
            
            contact = Inward(**{
                "content_type": content_type,
                "object_id": old_contact.node_id,                
                "status": 1,  # sent
                "from_name": old_contact.from_name,
                "from_email": old_contact.from_email,
                "message": old_contact.message,
                "ip": old_contact.ip,
                "user_agent": old_contact.user_agent,
                "accept_language": old_contact.accept_language,
                "added": old_contact.date,
                "updated": old_contact.date,
            })
            
            try:
                contact.full_clean()
                contact.save(auto_update=False)
                saved_contacts.append(contact)
                self.verbose('Saved contact log entry #%s' % contact.id)
            except Exception as e:
                self.message('Could not save contact log entry %s, got exception: %s' % (old_contact.id, e))
            
        self.message('saved %d entries of contact log into local DB' % len(saved_contacts))
        self.saved_contacts = saved_contacts