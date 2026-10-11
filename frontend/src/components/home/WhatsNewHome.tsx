import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import Routes from '../../configuration/routes/RouteList';
import { VideoType } from '../../pages/Home';
import formatDate from '../../functions/formatDates';
import { matchesQuery } from '../../functions/groupVideos';
import DataTable, { Column, SortState } from '../ux/DataTable';
import { ChipOption, FilterChips, SearchBox } from '../ux/Controls';
import VideoThumbnail from '../VideoThumbail';
import { WatchedToggle } from './VideoBits';

type WatchFilter = 'all' | 'unwatched' | 'watched';
type TypeFilter = 'all' | 'videos' | 'streams' | 'shorts';

const WATCH_CHIPS: ChipOption<WatchFilter>[] = [
  { value: 'all', label: 'All' },
  { value: 'unwatched', label: 'Not watched' },
  { value: 'watched', label: 'Watched' },
];

const TYPE_CHIPS: ChipOption<TypeFilter>[] = [
  { value: 'all', label: 'Any type' },
  { value: 'videos', label: 'Videos' },
  { value: 'streams', label: 'Livestreams' },
  { value: 'shorts', label: 'Shorts' },
];

const TYPE_NAMES: Record<string, string> = {
  videos: 'Video',
  streams: 'Livestream',
  shorts: 'Short',
};

const sortValue = (video: VideoType, key: string): string | number => {
  switch (key) {
    case 'title':
      return video.title.toLowerCase();
    case 'channel':
      return video.channel.channel_name.toLowerCase();
    case 'duration':
      return video.player?.duration ?? 0;
    case 'published':
      return video.published;
    case 'watched':
      return video.player?.watched ? 1 : 0;
    default:
      return video.date_downloaded;
  }
};

/** layout C: recent downloads as a searchable, sortable table */
const WhatsNewHome = ({ videos }: { videos: VideoType[] }) => {
  const [query, setQuery] = useState('');
  const [watch, setWatch] = useState<WatchFilter>('all');
  const [type, setType] = useState<TypeFilter>('all');
  const [sort, setSort] = useState<SortState>({ key: 'downloaded', order: 'desc' });

  const rows = useMemo(() => {
    const filtered = videos.filter(
      video =>
        matchesQuery(video, query) &&
        (watch === 'all' || Boolean(video.player?.watched) === (watch === 'watched')) &&
        (type === 'all' || video.vid_type === type),
    );
    const direction = sort.order === 'asc' ? 1 : -1;
    return filtered.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      return left < right ? -direction : left > right ? direction : 0;
    });
  }, [videos, query, watch, type, sort]);

  const columns: Column<VideoType>[] = [
    {
      key: 'thumb',
      header: '',
      className: 'ux-col-thumb',
      render: video => (
        <Link to={Routes.Video(video.youtube_id)} tabIndex={-1} aria-hidden="true">
          <VideoThumbnail videoThumbUrl={video.vid_thumb_url} />
        </Link>
      ),
    },
    {
      key: 'title',
      header: 'Title',
      sortKey: 'title',
      className: 'ux-col-title',
      render: video => <Link to={Routes.Video(video.youtube_id)}>{video.title}</Link>,
    },
    {
      key: 'channel',
      header: 'Channel',
      sortKey: 'channel',
      render: video => (
        <Link to={Routes.Channel(video.channel.channel_id)}>{video.channel.channel_name}</Link>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      render: video => TYPE_NAMES[video.vid_type] ?? video.vid_type,
    },
    {
      key: 'duration',
      header: 'Length',
      sortKey: 'duration',
      className: 'ux-num',
      render: video => video.player?.duration_str,
    },
    {
      key: 'published',
      header: 'Published',
      sortKey: 'published',
      render: video => formatDate(video.published),
    },
    {
      key: 'downloaded',
      header: 'Downloaded',
      sortKey: 'downloaded',
      render: video => formatDate(video.date_downloaded * 1000),
    },
    {
      key: 'watched',
      header: 'Watched',
      sortKey: 'watched',
      render: video => <WatchedToggle video={video} />,
    },
  ];

  return (
    <>
      <div className="ux-filterbar">
        <SearchBox
          label="Search recent downloads"
          placeholder="Search by title or channel…"
          value={query}
          onChange={setQuery}
        />
        <FilterChips label="Watched" options={WATCH_CHIPS} value={watch} onChange={setWatch} />
        <FilterChips label="Type" options={TYPE_CHIPS} value={type} onChange={setType} />
      </div>
      <p className="ux-count" aria-live="polite">
        {rows.length} of the newest {videos.length} downloads
      </p>
      <DataTable
        caption="Recent downloads"
        rows={rows}
        columns={columns}
        rowKey={video => video.youtube_id}
        sort={sort}
        onSortChange={setSort}
        empty="No downloads match."
      />
    </>
  );
};

export default WhatsNewHome;
