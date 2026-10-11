import { useCallback, useEffect, useState } from 'react';
import {
  clearVideoNumbering,
  clearVideoOverride,
  EffectiveRule,
  getEffectiveRule,
  getVideoNumbering,
  notBuilt,
  setVideoNumbering,
  setVideoOverride,
  VideoNumber,
} from '../../api/ux/client';
import { ApiError } from '../../functions/APIClient';

type EditState = { key: string; season: string; episode: string; error?: string };

/** a video's download choice and its Emby episode numbers, editable by hand */
const VideoChoices = ({ youtubeId }: { youtubeId: string }) => {
  const [effective, setEffective] = useState<EffectiveRule | null>();
  const [numbers, setNumbers] = useState<VideoNumber[]>([]);
  const [edit, setEdit] = useState<EditState | null>(null);

  const reload = useCallback(async () => {
    const rule = await getEffectiveRule({ video: youtubeId });
    if (notBuilt(rule)) {
      setEffective(null);
      return;
    }
    setEffective(rule.data ?? null);
    const numbering = await getVideoNumbering(youtubeId);
    setNumbers(numbering.data?.data ?? []);
  }, [youtubeId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  if (!effective) return null;

  const override = effective.override;
  const choose = async (choice: 'download' | 'ignore' | null) => {
    if (choice === null) {
      await clearVideoOverride(youtubeId);
    } else {
      await setVideoOverride(youtubeId, choice);
    }
    reload();
  };

  const saveNumber = async (row: VideoNumber) => {
    if (!edit) return;
    try {
      const response = await setVideoNumbering(youtubeId, {
        series_kind: row.series_kind,
        series_id: row.series_id,
        season: Number(edit.season),
        episode: Number(edit.episode),
      });
      if (response.status === 409) {
        setEdit({ ...edit, error: 'Another episode already has that number.' });
        return;
      }
      setEdit(null);
      reload();
    } catch (error) {
      setEdit({ ...edit, error: (error as ApiError).message });
    }
  };

  return (
    <div className="description-box ux-video-choices">
      <h2>Downloads and Emby</h2>
      <p>
        {override === 'download' && 'You chose to always download this video.'}
        {override === 'ignore' && 'You chose to always ignore this video.'}
        {!override &&
          (effective.source
            ? `Decided by the ${effective.source.scope} rule: ${effective.source.summary}.`
            : 'No rule applies; it downloads.')}
      </p>
      <div className="button-box">
        {override !== 'download' && (
          <button
            type="button"
            title="Download it whatever the rules say"
            onClick={() => choose('download')}
          >
            Download anyway
          </button>
        )}
        {override !== 'ignore' && (
          <button
            type="button"
            title="Never download it again, whatever the rules say"
            onClick={() => choose('ignore')}
          >
            Always ignore
          </button>
        )}
        {override && (
          <button type="button" className="ux-link-button" onClick={() => choose(null)}>
            Let the rules decide
          </button>
        )}
      </div>

      {numbers.length > 0 && (
        <table className="ux-table ux-compact">
          <caption>Episode numbers in Emby</caption>
          <thead>
            <tr>
              <th scope="col">Series</th>
              <th scope="col">Episode</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {numbers.map(row => {
              const key = `${row.series_kind}:${row.series_id}`;
              const editing = edit?.key === key;
              return (
                <tr key={key}>
                  <td>
                    {row.series_title} <span className="ux-muted">({row.series_kind})</span>
                  </td>
                  <td>
                    {editing ? (
                      <form
                        className="ux-inline-form"
                        onSubmit={event => {
                          event.preventDefault();
                          saveNumber(row);
                        }}
                      >
                        <label>
                          Season{' '}
                          <input
                            type="number"
                            min={0}
                            value={edit.season}
                            onChange={event => setEdit({ ...edit, season: event.target.value })}
                          />
                        </label>
                        <label>
                          Episode{' '}
                          <input
                            type="number"
                            min={0}
                            value={edit.episode}
                            onChange={event => setEdit({ ...edit, episode: event.target.value })}
                          />
                        </label>
                        <button type="submit">Save</button>
                        <button
                          type="button"
                          className="ux-link-button"
                          onClick={() => setEdit(null)}
                        >
                          Cancel
                        </button>
                        {edit.error && <span className="ux-failed">{edit.error}</span>}
                      </form>
                    ) : (
                      <>
                        <code>{row.name}</code>
                        {row.manual && <span className="ux-badge numbering">set by hand</span>}
                      </>
                    )}
                  </td>
                  <td>
                    {!editing && (
                      <div className="ux-row-actions">
                        <button
                          type="button"
                          className="ux-row-button"
                          title="Set the season and episode yourself; frozen schemes keep it"
                          onClick={() =>
                            setEdit({
                              key,
                              season: String(row.season),
                              episode: String(row.episode),
                            })
                          }
                        >
                          Set number…
                        </button>
                        {row.manual && (
                          <button
                            type="button"
                            className="ux-row-button"
                            title="Go back to the number the scheme gives"
                            onClick={async () => {
                              await clearVideoNumbering(youtubeId, row.series_kind, row.series_id);
                              reload();
                            }}
                          >
                            Automatic
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default VideoChoices;
