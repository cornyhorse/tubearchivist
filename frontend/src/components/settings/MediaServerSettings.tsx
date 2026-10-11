import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  getMediaServer,
  Library,
  listLibraries,
  MediaServer,
  MediaServerTest,
  notBuilt,
  notifyMediaServer,
  refreshMediaServer,
  saveMediaServer,
  testMediaServer,
} from '../../api/ux/client';
import Routes from '../../configuration/routes/RouteList';
import { ApiError } from '../../functions/APIClient';
import { ago } from '../../functions/timeText';
import SettingsSection from './SettingsSection';
import { SettingRow } from './SettingField';

type Draft = {
  type: 'emby' | 'jellyfin' | '';
  url: string;
  apiKey: string;
  libraryId: string;
  pathTo: string;
  notifyEnabled: boolean;
  debounce: number;
};

const fromServer = (server: MediaServer): Draft => ({
  type: server.type ?? '',
  url: server.url ?? '',
  apiKey: '',
  libraryId: server.library_id ?? '',
  pathTo: server.path_to ?? '',
  notifyEnabled: server.notify_enabled ?? true,
  debounce: server.debounce_seconds ?? 30,
});

/** Emby or Jellyfin connection: URL, write-only key, test, library, notify */
const MediaServerSettings = () => {
  const id = useId();
  const [server, setServer] = useState<MediaServer | null>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [test, setTest] = useState<MediaServerTest | 'testing' | null>(null);
  const [libraries, setLibraries] = useState<Library[] | string>();
  const [status, setStatus] = useState('');
  const [clearKey, setClearKey] = useState(false);

  useEffect(() => {
    (async () => {
      const response = await getMediaServer();
      if (notBuilt(response) || !response.data) {
        setServer(null);
        return;
      }
      setServer(response.data);
      setDraft(fromServer(response.data));
    })();
  }, []);

  const loadLibraries = async () => {
    try {
      const response = await listLibraries();
      if (response.status === 409) {
        setLibraries('Save the connection first.');
      } else if (response.status >= 400) {
        setLibraries(response.error?.error ?? 'The media server didn’t answer.');
      } else {
        setLibraries(response.data?.data ?? []);
      }
    } catch (error) {
      setLibraries((error as ApiError).message);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (server?.type) loadLibraries();
  }, [server?.type]);

  if (server === undefined) return null;
  if (server === null || !draft) {
    return (
      <SettingsSection id="media-server" title="Emby or Jellyfin">
        <p className="ux-muted">
          Connecting to Emby or Jellyfin comes with a newer server. Until then, Emby notices new
          files through its own library scans.
        </p>
      </SettingsSection>
    );
  }

  const update = (changes: Partial<Draft>) => setDraft({ ...draft, ...changes });
  const fromEnv = server.api_key_source === 'env';

  const save = async () => {
    setStatus('Saving…');
    const body: Parameters<typeof saveMediaServer>[0] = {
      type: draft.type || null,
      url: draft.url,
      library_id: draft.libraryId,
      path_to: draft.pathTo,
      notify_enabled: draft.notifyEnabled,
      debounce_seconds: draft.debounce,
    };
    if (draft.apiKey) body.api_key = draft.apiKey;
    if (clearKey) body.api_key = null;
    try {
      const response = await saveMediaServer(body);
      if (response.data) {
        setServer(response.data);
        setDraft(fromServer(response.data));
        setClearKey(false);
      }
      setStatus('Saved');
    } catch (error) {
      setStatus(`Not saved: ${(error as ApiError).message}`);
    }
  };

  const runTest = async () => {
    setTest('testing');
    const response = await testMediaServer({
      type: draft.type || null,
      url: draft.url,
      api_key: draft.apiKey || undefined,
    });
    setTest(
      response.data ?? {
        ok: false,
        error: 'No answer',
        server_name: null,
        product: null,
        version: null,
      },
    );
  };

  const last = server.last_notify;

  return (
    <SettingsSection
      id="media-server"
      title="Emby or Jellyfin"
      intro="When TA writes new episode files, it tells your media server about just those files, like Sonarr does. That's quicker than waiting for a library scan."
    >
      <SettingRow
        label="Media server"
        help="Which server to tell about new files. Off means TA only writes the files."
        htmlFor={`${id}-type`}
        isDefault={!draft.type}
      >
        <select
          id={`${id}-type`}
          value={draft.type}
          onChange={event => update({ type: event.target.value as Draft['type'] })}
        >
          <option value="">Off</option>
          <option value="emby">Emby</option>
          <option value="jellyfin">Jellyfin</option>
        </select>
      </SettingRow>

      {draft.type && (
        <>
          <SettingRow
            label="Address"
            help="The server’s URL as TA can reach it."
            htmlFor={`${id}-url`}
          >
            <input
              id={`${id}-url`}
              type="url"
              className="ux-wide-input"
              placeholder="http://emby:8096"
              value={draft.url}
              onChange={event => update({ url: event.target.value })}
            />
          </SettingRow>

          <SettingRow
            label="API key"
            help={
              fromEnv
                ? 'Set by the environment (TA_MEDIASERVER_API_KEY), so it can’t be changed here.'
                : 'Make one in the server’s dashboard under API keys. It’s never shown again after saving.'
            }
            htmlFor={`${id}-key`}
            effect={
              fromEnv
                ? 'Set by the environment.'
                : clearKey
                  ? 'Will be removed when you save.'
                  : server.api_key_set
                    ? 'A key is saved. Type a new one to replace it.'
                    : 'No key saved.'
            }
          >
            <div className="ux-inline-form">
              <input
                id={`${id}-key`}
                type="password"
                autoComplete="off"
                disabled={fromEnv}
                placeholder={server.api_key_set ? '••••••••' : ''}
                value={draft.apiKey}
                onChange={event => update({ apiKey: event.target.value })}
              />
              {server.api_key_set && !fromEnv && (
                <button
                  type="button"
                  className="ux-link-button"
                  onClick={() => setClearKey(!clearKey)}
                >
                  {clearKey ? 'Keep the key' : 'Remove the key'}
                </button>
              )}
            </div>
          </SettingRow>

          <SettingRow
            label="Test connection"
            help="Checks the address and key you typed, before saving."
          >
            <div className="ux-inline-form">
              <button type="button" onClick={runTest} disabled={test === 'testing' || !draft.url}>
                {test === 'testing' ? 'Testing…' : 'Test connection'}
              </button>
              <span role="status">
                {test && test !== 'testing' && test.ok && (
                  <span className="ux-ok">
                    ✓ Connected to {test.server_name} ({test.product} {test.version})
                  </span>
                )}
                {test && test !== 'testing' && !test.ok && (
                  <span className="ux-failed">✗ {test.error}</span>
                )}
              </span>
            </div>
          </SettingRow>

          <SettingRow
            label="Library"
            help="The library that shows TA’s videos. TA tells the server about files in it."
            htmlFor={`${id}-library`}
          >
            {typeof libraries === 'string' ? (
              <p className="ux-muted">{libraries}</p>
            ) : (
              <select
                id={`${id}-library`}
                value={draft.libraryId}
                onChange={event => update({ libraryId: event.target.value })}
              >
                <option value="">Choose…</option>
                {libraries?.map(library => (
                  <option key={library.id} value={library.id}>
                    {library.name}
                    {library.contains_ta ? ' (has TA’s folder)' : ''}
                  </option>
                ))}
              </select>
            )}
          </SettingRow>

          <SettingRow
            label="Folder as the server sees it"
            help={`TA writes to ${server.path_from}. If the server mounts that folder somewhere else, enter its path there.`}
            htmlFor={`${id}-path`}
          >
            <input
              id={`${id}-path`}
              type="text"
              className="ux-wide-input"
              placeholder={server.path_from}
              value={draft.pathTo}
              onChange={event => update({ pathTo: event.target.value })}
            />
          </SettingRow>

          <SettingRow
            label="Tell the server about new files"
            help="Batches changes and sends one update per batch. Never a full library scan."
            effect={
              last
                ? `Last update ${ago(last.at)}: ${last.error ? `failed, ${last.error}` : `answered ${last.status_code} for ${last.paths} files`}.`
                : 'No updates sent yet.'
            }
          >
            <div className="ux-inline-form">
              <label className="ux-switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={draft.notifyEnabled}
                  onChange={event => update({ notifyEnabled: event.target.checked })}
                />
                <span>{draft.notifyEnabled ? 'On' : 'Off'}</span>
              </label>
              <label className="ux-extra">
                wait{' '}
                <input
                  type="number"
                  min={0}
                  value={draft.debounce}
                  onChange={event => update({ debounce: Number(event.target.value) })}
                />{' '}
                seconds to batch
              </label>
            </div>
          </SettingRow>
        </>
      )}

      <div className="button-box">
        <button type="button" onClick={save}>
          Save
        </button>
        <span className="ux-setting-status" role="status">
          {status}
        </span>
      </div>

      {server.type && (
        <SettingRow
          label="Repairs"
          help="Send every file again, or ask the server to rescan the whole library. Both show on the Queue page."
        >
          <div className="button-box">
            <button
              type="button"
              onClick={async () => {
                await notifyMediaServer({ all: true });
                setStatus('Queued an update for every file.');
              }}
            >
              Tell it about every file
            </button>
            <button
              type="button"
              onClick={async () => {
                await refreshMediaServer(draft.libraryId || undefined);
                setStatus('Queued a full library refresh.');
              }}
            >
              Full library refresh
            </button>
            <Link to={Routes.Queue}>Open the queue</Link>
          </div>
        </SettingRow>
      )}
    </SettingsSection>
  );
};

export default MediaServerSettings;
