import { Link } from 'react-router-dom';
import Routes from '../../configuration/routes/RouteList';
import { VideoType } from '../../pages/Home';
import { groupByDay } from '../../functions/groupVideos';
import VideoThumbnail from '../VideoThumbail';
import { WatchedToggle } from './VideoBits';

/** layout B: downloads by day, then by channel, as compact rows */
const TimelineHome = ({ videos, now }: { videos: VideoType[]; now?: Date }) => {
  const days = groupByDay(videos, now);
  if (days.length === 0) return <p className="ux-muted">No videos downloaded yet.</p>;
  return (
    <div className="ux-timeline">
      {days.map(day => (
        <section key={day.key} className="ux-day" aria-labelledby={`day-${day.key}`}>
          <h2 id={`day-${day.key}`}>
            {day.label} <span className="ux-muted">· {day.count}</span>
          </h2>
          {day.channels.map(channel => (
            <div key={channel.channelId} className="ux-day-channel">
              <h3>
                <Link to={Routes.Channel(channel.channelId)}>{channel.channelName}</Link>
              </h3>
              <ul className="ux-rows">
                {channel.videos.map(video => (
                  <li
                    key={video.youtube_id}
                    className={video.player?.watched ? 'watched' : undefined}
                  >
                    <Link
                      to={Routes.Video(video.youtube_id)}
                      className="ux-row-thumb"
                      tabIndex={-1}
                      aria-hidden="true"
                    >
                      <VideoThumbnail videoThumbUrl={video.vid_thumb_url} />
                    </Link>
                    <Link to={Routes.Video(video.youtube_id)} className="ux-video-title">
                      {video.title}
                    </Link>
                    <span className="ux-muted ux-row-length">{video.player?.duration_str}</span>
                    <WatchedToggle video={video} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
};

export default TimelineHome;
