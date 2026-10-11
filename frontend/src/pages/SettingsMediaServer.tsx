import { useCallback, useEffect, useState } from 'react';
import Notifications from '../components/Notifications';
import PaginationDummy from '../components/PaginationDummy';
import SettingsNavigation from '../components/SettingsNavigation';
import SettingsSection from '../components/settings/SettingsSection';
import { SettingRow } from '../components/settings/SettingField';
import loadApiToken from '../api/loader/loadApiToken';
import deleteApiToken from '../api/actions/deleteApiToken';
import useAppSettings from '../functions/useAppSettings';
import { PLAYER_FIELDS } from '../configuration/settingsFields';

const SettingsMediaServer = () => {
  const { config, save } = useAppSettings();
  const [token, setToken] = useState<string>();
  const [showToken, setShowToken] = useState(false);

  const reloadToken = useCallback(async () => {
    const { data } = await loadApiToken();
    setToken(data?.token);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reloadToken();
  }, [reloadToken]);

  return (
    <>
      <title>TA | Media server</title>
      <div className="boxed-content">
        <SettingsNavigation />
        <Notifications pageName={'all'} />
        <div className="title-bar">
          <h1>Media server</h1>
          <p className="ux-help">
            How your videos reach Emby or Jellyfin and other apps. TA writes an Emby library
            (episode files and artwork) into the <code>.emby</code> folder next to your videos;
            point an Emby or Jellyfin “Shows” library at it. Episode numbering is set per channel
            and playlist.
          </p>
        </div>

        <SettingsSection id="apps" title="Other apps">
          <SettingRow
            label="API token"
            help="Lets other apps and scripts use TA’s API as you. Keep it secret."
            effect={token ? 'A token exists.' : 'No token yet; one is made when you show it.'}
          >
            <div className="ux-inline-form">
              {showToken && <input readOnly aria-label="API token" value={token ?? ''} />}
              <button type="button" onClick={() => setShowToken(!showToken)}>
                {showToken ? 'Hide' : 'Show token'}
              </button>
              {showToken && (
                <button
                  type="button"
                  className="danger-button"
                  title="Makes a new token; apps using the old one stop working"
                  onClick={async () => {
                    await deleteApiToken();
                    setShowToken(false);
                    reloadToken();
                  }}
                >
                  Replace token
                </button>
              )}
            </div>
          </SettingRow>
        </SettingsSection>

        <SettingsSection
          id="player"
          title="Playing in the browser"
          fields={PLAYER_FIELDS}
          config={config}
          onSave={save}
        />
      </div>
      <PaginationDummy />
    </>
  );
};

export default SettingsMediaServer;
