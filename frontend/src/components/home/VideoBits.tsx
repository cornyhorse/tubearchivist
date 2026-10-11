import { useState } from 'react';
import { Link } from 'react-router-dom';
import Routes from '../../configuration/routes/RouteList';
import updateWatchedState from '../../api/actions/updateWatchedState';
import { VideoType } from '../../pages/Home';
import formatDate from '../../functions/formatDates';
import VideoThumbnail from '../VideoThumbail';

type WatchedToggleProps = {
  video: VideoType;
  onChanged?: (watched: boolean) => void;
};

/** a real button for watched / not watched */
export const WatchedToggle = ({ video, onChanged }: WatchedToggleProps) => {
  const [watched, setWatched] = useState(video.player?.watched ?? false);
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className={`ux-watched ${watched ? 'on' : ''}`}
      aria-pressed={watched}
      aria-label={`Watched: ${video.title}`}
      title={watched ? 'Watched. Click to mark as not watched.' : 'Mark as watched'}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await updateWatchedState({ id: video.youtube_id, is_watched: !watched });
        setWatched(!watched);
        setBusy(false);
        onChanged?.(!watched);
      }}
    >
      <span aria-hidden="true">{watched ? '✓' : ''}</span>
    </button>
  );
};

/** a compact card for shelves: thumbnail, title, length, upload date */
export const VideoCard = ({ video, isNew }: { video: VideoType; isNew?: boolean }) => (
  <article className={`ux-video-card ${video.player?.watched ? 'watched' : ''}`}>
    <Link
      to={Routes.Video(video.youtube_id)}
      className="ux-video-card-thumb"
      tabIndex={-1}
      aria-hidden="true"
    >
      <VideoThumbnail videoThumbUrl={video.vid_thumb_url} />
      {video.player?.duration_str && (
        <span className="ux-duration">{video.player.duration_str}</span>
      )}
      {isNew && <span className="ux-new">New</span>}
    </Link>
    <div className="ux-video-card-text">
      <Link to={Routes.Video(video.youtube_id)} className="ux-video-title">
        {video.title}
      </Link>
      <div className="ux-video-meta">
        <span>{formatDate(video.published)}</span>
        <WatchedToggle video={video} />
      </div>
    </div>
  </article>
);
