import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EmbyTagsSelect from './EmbyTagsSelect';

const save = vi.fn();
vi.mock('../../api/actions/updateEmbyTags', () => ({
  default: (...args: unknown[]) => save(...args),
}));

afterEach(() => {
  cleanup();
  save.mockReset();
});

describe('EmbyTagsSelect', () => {
  it('is plain text without edit rights, Hidden when untagged', () => {
    render(
      <EmbyTagsSelect
        kind="channel"
        id="c1"
        name="Trains"
        value={undefined}
        knownTags={['David']}
        onSaved={() => {}}
      />,
    );
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText('Hidden').getAttribute('title')).toContain('like David');
  });

  it('shows the tags as text for viewers', () => {
    render(
      <EmbyTagsSelect
        kind="channel"
        id="c1"
        name="Trains"
        value={['David']}
        knownTags={['David']}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByText('David')).toBeTruthy();
  });

  it('offers one labelled switch per known tag and adds the tag, keeping others', async () => {
    save.mockResolvedValue({ status: 200 });
    const onSaved = vi.fn();
    render(
      <EmbyTagsSelect
        kind="playlist"
        id="PL1"
        name="Coasters"
        value={['Other']}
        knownTags={['David', 'Ann']}
        canEdit
        onSaved={onSaved}
      />,
    );
    expect(screen.getAllByRole('switch')).toHaveLength(2);
    expect(screen.getByText('Visible to David')).toBeTruthy();
    expect(screen.getByText('Other')).toBeTruthy();
    const toggle = screen.getByRole('switch', { name: 'Visible to David: Coasters' });
    expect((toggle as HTMLInputElement).checked).toBe(false);
    expect(toggle.getAttribute('aria-describedby')).toBeTruthy();

    fireEvent.click(toggle);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith('playlist', 'PL1', ['Other', 'David']);
  });

  it('treats a tag spelled in another case as the same tag', async () => {
    save.mockResolvedValue({ status: 200 });
    render(
      <EmbyTagsSelect
        kind="channel"
        id="UC1"
        name="Trains"
        value={['david']}
        knownTags={['David']}
        canEdit
        onSaved={vi.fn()}
      />,
    );
    const toggle = screen.getByRole('switch', { name: 'Visible to David: Trains' });
    expect((toggle as HTMLInputElement).checked).toBe(true);

    fireEvent.click(toggle);

    await waitFor(() => expect(save).toHaveBeenCalledWith('channel', 'UC1', []));
  });

  it('removes only the toggled tag', async () => {
    save.mockResolvedValue({ status: 200 });
    render(
      <EmbyTagsSelect
        kind="channel"
        id="c1"
        name="Trains"
        value={['David', 'Other']}
        knownTags={['David']}
        canEdit
        onSaved={() => {}}
      />,
    );
    const toggle = screen.getByRole('switch');
    expect((toggle as HTMLInputElement).checked).toBe(true);

    fireEvent.click(toggle);

    await waitFor(() => expect(save).toHaveBeenCalledWith('channel', 'c1', ['Other']));
  });

  it('reports a failed or rejected save', async () => {
    save.mockRejectedValueOnce({ status: 400 }).mockResolvedValueOnce({ status: 500 });
    const onSaved = vi.fn();
    render(
      <EmbyTagsSelect
        kind="channel"
        id="c1"
        name="Trains"
        value={[]}
        knownTags={['David']}
        canEdit
        onSaved={onSaved}
      />,
    );

    fireEvent.click(screen.getByRole('switch'));
    expect((await screen.findByRole('alert')).textContent).toContain('Could not save');

    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('says Hidden when nothing is offered or set', () => {
    render(
      <EmbyTagsSelect
        kind="channel"
        id="c1"
        name="Trains"
        value={[]}
        knownTags={[]}
        canEdit
        onSaved={() => {}}
      />,
    );
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText('Hidden')).toBeTruthy();
  });
});
