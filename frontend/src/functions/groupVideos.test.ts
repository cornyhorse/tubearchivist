import { describe, expect, it } from 'vitest';
import { VideoType } from '../pages/Home';
import { dayLabel, groupByChannel, groupByDay, isNew, matchesQuery } from './groupVideos';

// Saturday 10 October 2026, 14:00
const now = new Date(2026, 9, 10, 14, 0);
const nowSeconds = now.getTime() / 1000;
const daysAgo = (days: number) => nowSeconds - days * 86400;

const video = (
  id: string,
  channel: string,
  published: string,
  downloaded: number,
  watched = false,
) =>
  ({
    youtube_id: id,
    title: `Title ${id}`,
    published,
    date_downloaded: downloaded,
    vid_type: 'videos',
    vid_thumb_url: '',
    player: { watched, duration: 60, duration_str: '1m', progress: 0, position: 0 },
    channel: { channel_id: channel, channel_name: `Channel ${channel}`, channel_thumb_url: '' },
  }) as unknown as VideoType;

const videos = [
  video('a1', 'A', '2026-10-01', daysAgo(0)),
  video('b1', 'B', '2026-10-09', daysAgo(1), true),
  video('a2', 'A', '2026-09-01', daysAgo(10)),
  video('b2', 'B', '2026-10-05', daysAgo(3)),
  video('c1', 'C', '2026-01-01', daysAgo(40)),
];

describe('isNew', () => {
  it('is unwatched and downloaded this week', () => {
    expect(isNew(videos[0], nowSeconds)).toBe(true);
    expect(isNew(videos[1], nowSeconds)).toBe(false);
    expect(isNew(videos[2], nowSeconds)).toBe(false);
  });
});

describe('groupByChannel', () => {
  it('orders channels by latest upload and videos newest first', () => {
    const shelves = groupByChannel(videos, 8, nowSeconds);
    expect(shelves.map(shelf => shelf.channelId)).toEqual(['B', 'A', 'C']);
    expect(shelves[0].videos.map(item => item.youtube_id)).toEqual(['b1', 'b2']);
    expect(shelves[0].newCount).toBe(1);
    expect(shelves[1].newCount).toBe(1);
  });

  it('caps each shelf', () => {
    expect(groupByChannel(videos, 1, nowSeconds)[1].videos).toHaveLength(1);
  });

  it('defaults the shelf size and clock', () => {
    expect(groupByChannel(videos)).toHaveLength(3);
  });
});

describe('dayLabel', () => {
  it('names recent days and months', () => {
    expect(dayLabel(new Date(2026, 9, 10, 1), now).label).toBe('Today');
    expect(dayLabel(new Date(2026, 9, 9, 23), now).label).toBe('Yesterday');
    expect(dayLabel(new Date(2026, 9, 7), now).key).toBe('d3');
    expect(dayLabel(new Date(2026, 7, 7), now).key).toBe('m2026-08');
    expect(dayLabel(new Date(2026, 9, 11), now).label).toBe('Today');
  });
});

describe('groupByDay', () => {
  it('groups by day, then channel, newest first', () => {
    const days = groupByDay(videos, now);
    expect(days.map(day => day.label.length > 0 && day.key)).toEqual([
      'd0',
      'd1',
      'd3',
      'm2026-09',
      'm2026-08',
    ]);
    expect(days[0].channels[0].channelId).toBe('A');
    expect(days.reduce((total, day) => total + day.count, 0)).toBe(5);
  });

  it('puts several channels in one day', () => {
    const same = [
      video('x', 'A', '2026-10-01', daysAgo(0)),
      video('y', 'B', '2026-10-01', daysAgo(0) - 60),
      video('z', 'A', '2026-10-01', daysAgo(0) - 120),
    ];
    const [today] = groupByDay(same, now);
    expect(today.channels.map(channel => channel.videos.length)).toEqual([2, 1]);
  });

  it('defaults the clock', () => {
    expect(groupByDay([])).toEqual([]);
  });
});

describe('matchesQuery', () => {
  it('matches title or channel, ignoring case', () => {
    expect(matchesQuery(videos[0], '')).toBe(true);
    expect(matchesQuery(videos[0], 'title a1')).toBe(true);
    expect(matchesQuery(videos[0], 'CHANNEL a')).toBe(true);
    expect(matchesQuery(videos[0], 'nope')).toBe(false);
  });
});
