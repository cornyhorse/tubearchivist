import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChannelOverview from './ChannelOverview';

vi.mock('../functions/useIsAdmin', () => ({ default: () => true }));
vi.mock('./ChannelIcon', () => ({ default: () => null }));

afterEach(cleanup);

const show = (archived: boolean) =>
  render(
    <MemoryRouter>
      <ChannelOverview
        channelId="arch_music"
        channelname="Music"
        channelSubs={5}
        channelSubscribed={false}
        channelThumbUrl=""
        archived={archived}
        setRefresh={() => {}}
      />
    </MemoryRouter>,
  );

describe('ChannelOverview', () => {
  it('offers to subscribe to a YouTube channel', () => {
    show(false);
    expect(screen.getByText('Subscribe')).toBeTruthy();
    expect(screen.getByText(/Subscribers/)).toBeTruthy();
  });

  it('has nothing to subscribe to for an archived channel', () => {
    show(true);
    expect(screen.getByText('Music')).toBeTruthy();
    expect(screen.queryByText('Subscribe')).toBeNull();
    expect(screen.queryByText(/Subscribers/)).toBeNull();
  });
});
