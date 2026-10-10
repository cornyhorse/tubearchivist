"""shared SQL objects: pg_trgm and the immutable array text helper"""

from common.src.db import CREATE_ARRAY_TEXT_FN, DROP_ARRAY_TEXT_FN
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """create extensions and helper functions"""

    initial = True
    dependencies: list = []

    operations = [
        TrigramExtension(),
        migrations.RunSQL(CREATE_ARRAY_TEXT_FN, DROP_ARRAY_TEXT_FN),
    ]
