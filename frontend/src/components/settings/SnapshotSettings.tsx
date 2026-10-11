import { useCallback, useEffect, useState } from 'react';
import loadSnapshots, { SnapshotListType } from '../../api/loader/loadSnapshots';
import queueSnapshot from '../../api/actions/queueSnapshot';
import restoreSnapshot from '../../api/actions/restoreSnapshot';
import useAppSettings from '../../functions/useAppSettings';
import { SNAPSHOT_FIELDS } from '../../configuration/settingsFields';
import SettingsSection from './SettingsSection';

/** database snapshots: the setting, "make one now", and restore */
const SnapshotSettings = () => {
  const { config, save } = useAppSettings();
  const [snapshots, setSnapshots] = useState<SnapshotListType>();
  const [visible, setVisible] = useState(10);
  const [queued, setQueued] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const { data } = await loadSnapshots();
    setSnapshots(data);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  const enabled = config?.application.enable_snapshot;
  const list = snapshots?.snapshots ?? [];

  return (
    <SettingsSection
      id="snapshots"
      title="Database snapshots"
      fields={SNAPSHOT_FIELDS}
      config={config}
      onSave={save}
    >
      {enabled && snapshots && (
        <>
          <p className="ux-setting-effect">
            Next snapshot {snapshots.next_exec_str}; snapshots are kept for {snapshots.expire_after}
            .
          </p>
          <div className="button-box">
            <button
              type="button"
              disabled={queued}
              onClick={async () => {
                setQueued(true);
                await queueSnapshot();
              }}
            >
              {queued ? 'Snapshot started' : 'Make a snapshot now'}
            </button>
          </div>
          {restoring && <p role="status">Restoring the snapshot…</p>}
          {!restoring && list.length > 0 && (
            <table className="ux-table ux-compact">
              <caption className="sr-only">Snapshots</caption>
              <thead>
                <tr>
                  <th scope="col">Made</th>
                  <th scope="col">Took</th>
                  <th scope="col">State</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.slice(0, visible).map(snapshot => (
                  <tr key={snapshot.id}>
                    <td>{snapshot.start_date}</td>
                    <td>{snapshot.duration_s} s</td>
                    <td>{snapshot.state}</td>
                    <td>
                      {confirmId === snapshot.id ? (
                        <>
                          <button
                            type="button"
                            className="danger-button"
                            onClick={async () => {
                              setRestoring(true);
                              await restoreSnapshot(snapshot.id);
                            }}
                          >
                            Replace the database
                          </button>{' '}
                          <button
                            type="button"
                            className="ux-link-button"
                            onClick={() => setConfirmId(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="ux-row-button"
                          title="Replaces the current database with this snapshot"
                          onClick={() => setConfirmId(snapshot.id)}
                        >
                          Restore…
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {visible < list.length && (
            <button type="button" onClick={() => setVisible(visible + 10)}>
              Show more
            </button>
          )}
        </>
      )}
    </SettingsSection>
  );
};

export default SnapshotSettings;
