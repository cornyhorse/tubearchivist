import { useCallback, useEffect, useState } from 'react';
import Notifications from '../components/Notifications';
import PaginationDummy from '../components/PaginationDummy';
import SettingsNavigation from '../components/SettingsNavigation';
import SettingsSection from '../components/settings/SettingsSection';
import { SettingRow } from '../components/settings/SettingField';
import loadCookie, { CookieStateType } from '../api/loader/loadCookie';
import updateCookie from '../api/actions/updateCookie';
import deleteCookie from '../api/actions/deleteCookie';
import validateCookie from '../api/actions/validateCookie';
import useAppSettings from '../functions/useAppSettings';
import { YOUTUBE_FIELDS } from '../configuration/settingsFields';

const SettingsYouTube = () => {
  const { config, save } = useAppSettings();
  const [cookie, setCookie] = useState<CookieStateType>();
  const [cookieText, setCookieText] = useState('');
  const [showCookieForm, setShowCookieForm] = useState(false);

  const reload = useCallback(async () => {
    const { data } = await loadCookie();
    setCookie(data);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  return (
    <>
      <title>TA | YouTube access</title>
      <div className="boxed-content">
        <SettingsNavigation />
        <Notifications pageName={'all'} />
        <div className="title-bar">
          <h1>YouTube access</h1>
          <p className="ux-help">
            How TA talks to YouTube. Only needed when downloads fail with “sign in to confirm” or
            similar errors.
          </p>
        </div>

        <SettingsSection id="cookie" title="Your YouTube cookie">
          <SettingRow
            label="Sign in with your cookie"
            help={
              <>
                Makes requests as your YouTube account, which avoids many blocks. Paste a cookie in
                Netscape format, or send it with the{' '}
                <a href="https://github.com/tubearchivist/browser-extension" target="_blank">
                  browser extension
                </a>
                .
              </>
            }
            isDefault={!cookie?.cookie_enabled}
            effect={
              cookie?.cookie_enabled
                ? `On. Last checked ${cookie.validated_str ?? 'never'}${
                    cookie.status === false ? ', and it didn’t work' : ''
                  }.`
                : 'Off. Requests are anonymous.'
            }
          >
            <div className="button-box">
              <button type="button" onClick={() => setShowCookieForm(!showCookieForm)}>
                {cookie?.cookie_enabled ? 'Replace cookie…' : 'Add cookie…'}
              </button>
              {cookie?.cookie_enabled && (
                <>
                  <button
                    type="button"
                    title="Asks YouTube whether the cookie still works"
                    onClick={async () => {
                      await validateCookie();
                      reload();
                    }}
                  >
                    Check it works
                  </button>
                  <button
                    type="button"
                    className="danger-button"
                    title="Forgets the cookie; requests become anonymous"
                    onClick={async () => {
                      await deleteCookie();
                      reload();
                    }}
                  >
                    Remove cookie
                  </button>
                </>
              )}
            </div>
            {showCookieForm && (
              <div className="ux-cookie-form">
                <label htmlFor="cookie-text">Cookie (Netscape format)</label>
                <textarea
                  id="cookie-text"
                  rows={4}
                  value={cookieText}
                  onChange={event => setCookieText(event.target.value)}
                />
                <div className="button-box">
                  <button
                    type="button"
                    disabled={!cookieText.trim()}
                    onClick={async () => {
                      await updateCookie(cookieText);
                      setCookieText('');
                      setShowCookieForm(false);
                      reload();
                    }}
                  >
                    Save cookie
                  </button>
                  <button type="button" onClick={() => setShowCookieForm(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </SettingRow>
        </SettingsSection>

        <SettingsSection
          id="po-token"
          title="PO token"
          intro={
            <>
              See the{' '}
              <a
                target="_blank"
                href="https://docs.tubearchivist.com/settings/application/#po-token-provider-url"
              >
                user guide
              </a>{' '}
              before setting this.
            </>
          }
          fields={YOUTUBE_FIELDS}
          config={config}
          onSave={save}
        />
      </div>
      <PaginationDummy />
    </>
  );
};

export default SettingsYouTube;
