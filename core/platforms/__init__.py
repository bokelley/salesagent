"""Per-tenant DecisioningPlatform implementations.

Each subclass declares its capabilities and implements specialism methods
(``get_products``, ``create_media_buy``, ``sync_creatives``, etc.). The
framework's PlatformRouter dispatches per request based on the resolved
account's tenant.
"""
