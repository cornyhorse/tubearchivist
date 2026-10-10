"""application level settings"""

from django.db import models


class ConfigDocument(models.Model):
    """
    app wide settings documents, validated by their serializers:
    'appsettings' (AppConfig) and 'notify' (task notification urls)
    """

    key = models.CharField(primary_key=True, max_length=64)
    value = models.JSONField(default=dict)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key
