import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CheckSourceButton from './CheckSourceButton';
import { createJob } from '../api/ux/client';

vi.mock('../api/ux/client', () => ({ createJob: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.mocked(createJob).mockReset();
});

const show = (kind: 'channel' | 'playlist') =>
  render(
    <MemoryRouter>
      <CheckSourceButton kind={kind} id="UC1" name="Music" />
    </MemoryRouter>,
  );

describe('CheckSourceButton', () => {
  it('queues a check of this channel only', async () => {
    vi.mocked(createJob).mockResolvedValue({ status: 201, data: { job: null, merged: false } });
    show('channel');

    fireEvent.click(screen.getByText('Check for new videos'));

    expect(await screen.findByText(/Check queued/)).toBeTruthy();
    expect(createJob).toHaveBeenCalledWith({ type: 'check_channel', target_id: 'UC1' });
  });

  it('says when a waiting check was moved up', async () => {
    vi.mocked(createJob).mockResolvedValue({ status: 200, data: { job: null, merged: true } });
    show('playlist');

    fireEvent.click(screen.getByText('Check for new videos'));

    expect(await screen.findByText(/already waiting/)).toBeTruthy();
    expect(createJob).toHaveBeenCalledWith({ type: 'check_playlist', target_id: 'UC1' });
  });

  it('reports a failure', async () => {
    vi.mocked(createJob).mockResolvedValue({ status: 500, error: { error: 'boom' } });
    show('channel');

    fireEvent.click(screen.getByText('Check for new videos'));

    expect(await screen.findByText(/Could not queue/)).toBeTruthy();
  });
});
