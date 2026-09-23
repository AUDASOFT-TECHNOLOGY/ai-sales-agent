"""WhatsApp Business Cloud API adapters: sending, and webhook authentication and parsing."""

from sales_integrations.whatsapp.channel import WhatsAppChannel
from sales_integrations.whatsapp.webhook import WhatsAppWebhookParser, compute_signature

__all__ = ["WhatsAppChannel", "WhatsAppWebhookParser", "compute_signature"]
