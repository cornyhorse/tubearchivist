"""emby writer state"""

from django.db import models


class SeriesKind(models.TextChoices):
    """what an Emby series is built from"""

    CHANNEL = "channel"
    PLAYLIST = "playlist"


class EpisodeNumber(models.Model):
    """
    the season and episode assigned to a video within a series and scheme
    assigned once, never changed except by an explicit renumber
    numbers set by hand are stored with scheme "manual"; every scheme of
    the series keeps them
    """

    series_kind = models.CharField(max_length=16, choices=SeriesKind.choices)
    series_id = models.CharField(max_length=64)
    scheme = models.CharField(max_length=32)
    youtube_id = models.CharField(max_length=32)
    season = models.IntegerField()
    episode = models.IntegerField()
    assigned = models.DateTimeField(auto_now_add=True)
    # file names are frozen when first written, so a renamed video or
    # series doesn't move files in Emby; titles live in the NFOs only
    # episode part of the file name, filesystem safe
    name = models.CharField(max_length=255, blank=True, default="")
    # series folder name, the same on every row of the series
    series_name = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series_kind", "series_id", "scheme", "youtube_id"],
                name="episode_number_one_per_video",
            ),
            models.UniqueConstraint(
                fields=[
                    "series_kind",
                    "series_id",
                    "scheme",
                    "season",
                    "episode",
                ],
                name="episode_number_unique_slot",
            ),
        ]

    def __str__(self):
        return (
            f"{self.series_id}/{self.scheme}: {self.youtube_id} "
            f"S{self.season}E{self.episode}"
        )
