import { Link } from 'react-router-dom';
import Routes from '../../configuration/routes/RouteList';
import { VideoType } from '../../pages/Home';
import { groupByChannel, isNew } from '../../functions/groupVideos';
import ChannelIcon from '../ChannelIcon';
import { VideoCard } from './VideoBits';

/** layout A: one shelf per channel, newest uploads first */
const ShelvesHome = ({ videos, now }: { videos: VideoType[]; now?: number }) => {
  const shelves = groupByChannel(videos, 8, now);
  if (shelves.length === 0) return <p className="ux-muted">No videos downloaded yet.</p>;
  return (
    <div className="ux-shelves">
      {shelves.map(shelf => (
        <section
          key={shelf.channelId}
          className="ux-shelf"
          aria-labelledby={`shelf-${shelf.channelId}`}
        >
          <header className="ux-shelf-head">
            <span className="ux-shelf-avatar" aria-hidden="true">
              <ChannelIcon channelId={shelf.channelId} channelThumbUrl={shelf.channelThumbUrl} />
            </span>
            <h2 id={`shelf-${shelf.channelId}`}>
              <Link to={Routes.Channel(shelf.channelId)}>{shelf.channelName}</Link>
            </h2>
            {shelf.newCount > 0 && (
              <span className="ux-badge rule own" title="Downloaded this week and not watched yet">
                {shelf.newCount} new
              </span>
            )}
          </header>
          <div className="ux-shelf-strip" role="list">
            {shelf.videos.map(video => (
              <div role="listitem" key={video.youtube_id}>
                <VideoCard video={video} isNew={isNew(video, now)} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
};

export default ShelvesHome;
