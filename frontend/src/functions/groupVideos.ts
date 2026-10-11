import { VideoType } from '../pages/Home';

export type ChannelShelf = {
  channelId: string;
  channelName: string;
  channelThumbUrl: string | undefined;
  /** newest first */
  videos: VideoType[];
  /** unwatched and downloaded within the last week */
  newCount: number;
  latestPublished: string;
};

const WEEK = 7 * 24 * 3600;

export const isNew = (video: VideoType, nowSeconds = Date.now() / 1000) =>
  !video.player?.watched && nowSeconds - video.date_downloaded < WEEK;

const byPublishedDesc = (a: VideoType, b: VideoType) => b.published.localeCompare(a.published);

/** one shelf per channel, channels with the latest upload first */
export const groupByChannel = (
  videos: VideoType[],
  perShelf = 8,
  nowSeconds = Date.now() / 1000,
): ChannelShelf[] => {
  const shelves = new Map<string, ChannelShelf>();
  for (const video of videos) {
    const id = video.channel.channel_id;
    let shelf = shelves.get(id);
    if (!shelf) {
      shelf = {
        channelId: id,
        channelName: video.channel.channel_name,
        channelThumbUrl: video.channel.channel_thumb_url,
        videos: [],
        newCount: 0,
        latestPublished: video.published,
      };
      shelves.set(id, shelf);
    }
    shelf.videos.push(video);
    if (isNew(video, nowSeconds)) shelf.newCount += 1;
    if (video.published > shelf.latestPublished) shelf.latestPublished = video.published;
  }
  return Array.from(shelves.values())
    .map(shelf => ({ ...shelf, videos: shelf.videos.sort(byPublishedDesc).slice(0, perShelf) }))
    .sort((a, b) => b.latestPublished.localeCompare(a.latestPublished));
};

export type DayGroup = {
  key: string;
  label: string;
  channels: { channelId: string; channelName: string; videos: VideoType[] }[];
  count: number;
};

const startOfDay = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/** "Today", "Yesterday", weekday names this week, then "Earlier" by month */
export const dayLabel = (when: Date, now = new Date()): { key: string; label: string } => {
  const days = Math.round((startOfDay(now) - startOfDay(when)) / 86400000);
  if (days <= 0) return { key: 'd0', label: 'Today' };
  if (days === 1) return { key: 'd1', label: 'Yesterday' };
  if (days < 7) {
    return { key: `d${days}`, label: when.toLocaleDateString(undefined, { weekday: 'long' }) };
  }
  const key = `m${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, '0')}`;
  const label = when.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  return { key, label };
};

/** downloads grouped by day, and by channel within a day; newest first */
export const groupByDay = (videos: VideoType[], now = new Date()): DayGroup[] => {
  const groups: DayGroup[] = [];
  const sorted = [...videos].sort((a, b) => b.date_downloaded - a.date_downloaded);
  for (const video of sorted) {
    const { key, label } = dayLabel(new Date(video.date_downloaded * 1000), now);
    let group = groups.find(item => item.key === key);
    if (!group) {
      group = { key, label, channels: [], count: 0 };
      groups.push(group);
    }
    let channel = group.channels.find(item => item.channelId === video.channel.channel_id);
    if (!channel) {
      channel = {
        channelId: video.channel.channel_id,
        channelName: video.channel.channel_name,
        videos: [],
      };
      group.channels.push(channel);
    }
    channel.videos.push(video);
    group.count += 1;
  }
  return groups;
};

/** case-insensitive match on title or channel name */
export const matchesQuery = (video: VideoType, query: string) => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return (
    video.title.toLowerCase().includes(needle) ||
    video.channel.channel_name.toLowerCase().includes(needle)
  );
};
