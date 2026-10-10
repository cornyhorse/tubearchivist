"""interact with queue items"""

from download.models import Download


class PendingInteract:
    """interact with items in download queue"""

    def __init__(self, youtube_id=False, status=False):
        self.youtube_id = youtube_id
        self.status = status

    def delete_item(self) -> None:
        """delete single item from pending"""
        Download.objects.filter(youtube_id=self.youtube_id).delete()

    def delete_bulk(self, channel_id: str | None, vid_type: str | None):
        """delete all matching item by status"""
        Download.objects.matching(
            status=self.status, channel_id=channel_id, vid_type=vid_type
        ).delete()

    def update_bulk(
        self,
        channel_id: str | None,
        vid_type: str | None,
        new_status: str,
        error: bool | None = None,
    ):
        """update status in bulk"""
        Download.objects.matching(
            status=self.status,
            channel_id=channel_id,
            vid_type=vid_type,
            error=error,
        ).set_status(new_status)

    def update_status(self):
        """update status of pending item"""
        Download.objects.filter(youtube_id=self.youtube_id).set_status(
            self.status
        )

    def get_item(self) -> tuple[dict | None, int]:
        """return pending item dict and a status code"""
        item = (
            Download.objects.with_channel_indexed()
            .filter(youtube_id=self.youtube_id)
            .first()
        )
        if not item:
            return None, 404
        return item.to_doc(), 200

    def get_channel(self):
        """
        get channel metadata from queue to not depend on channel to be indexed
        """
        item = Download.objects.filter(channel_id=self.youtube_id).first()
        channel_name = item.channel_name if item else "NA"

        return {
            "channel_id": self.youtube_id,
            "channel_name": channel_name,
        }
