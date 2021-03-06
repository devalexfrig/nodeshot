from django.conf import settings


DEFAULT_SYNCHRONIZERS = [
    ('nodeshot.interoperability.synchronizers.Nodeshot', 'Nodeshot'),
    ('nodeshot.interoperability.synchronizers.GeoJson', 'GeoJSON'),
    ('nodeshot.interoperability.synchronizers.GeoRss', 'GeoRSS'),
    ('nodeshot.interoperability.synchronizers.OpenWISP', 'OpenWISP'),
    ('nodeshot.interoperability.synchronizers.OpenWISPCitySDK', 'OpenWISPCitySDK'),
    ('nodeshot.interoperability.synchronizers.ProvinciaWIFI', 'Provincia WiFi'),
    ('nodeshot.interoperability.synchronizers.ProvinciaWIFICitySDK', 'ProvinciaWIFICitySDK'),
    ('nodeshot.interoperability.synchronizers.ProvinciaWifiCitySdkMobility', 'Synchronize Provincia Wifi with CitySDK Mobility'),
    ('nodeshot.interoperability.synchronizers.CitySdkMobility', 'CitySDK Mobility (event driven)'),
    ('nodeshot.interoperability.synchronizers.GeoJsonCitySdkMobility', 'Import GeoJSON into CitySDK Mobility API'),
    ('nodeshot.interoperability.synchronizers.GeoJsonCitySdkTourism', 'Import GeoJSON into CitySDK Tourism API'),
    ('nodeshot.interoperability.synchronizers.OpenLabor', 'OpenLabor'),
]

SYNCHRONIZERS = DEFAULT_SYNCHRONIZERS + getattr(settings, 'NODESHOT_SYNCHRONIZERS', [])
