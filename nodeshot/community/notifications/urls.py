from django.conf.urls import patterns, url
from django.conf import settings


urlpatterns = patterns('nodeshot.community.notifications.views',
    url(r'^/account/notifications/$', 'notification_list', name='api_notification_list'),
    
    # email settings
    url(r'^/account/notifications/email-settings/$',
        'notification_email_settings',
        name='api_notification_email_settings'),
    
    # web settings
    url(r'^/account/notifications/web-settings/$',
        'notification_web_settings',
        name='api_notification_web_settings'),
)