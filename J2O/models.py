from django.db import models
import uuid

class IDs(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    id_list_field = models.JSONField(default=list)

    def __str__(self):
        return str(self.id_list_field)

