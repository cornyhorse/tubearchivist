import { useCallback, useEffect, useState } from 'react';
import Notifications from '../components/Notifications';
import PaginationDummy from '../components/PaginationDummy';
import SettingsNavigation from '../components/SettingsNavigation';
import SettingsSection from '../components/settings/SettingsSection';
import MembershipAppsettings from '../components/MembershipAppsettings';
import loadAppriseNotification, {
  AppriseNotificationType,
} from '../api/loader/loadAppriseNotification';
import createAppriseNotificationUrl, {
  AppriseTaskNameType,
} from '../api/actions/createAppriseNotificationUrl';
import deleteAppriseNotificationUrl from '../api/actions/deleteAppriseNotificationUrl';
import testAppriseNotificationUrl from '../api/actions/testAppriseNotificationUrl';
import { ApiError } from '../functions/APIClient';
import { useUserConfigStore } from '../stores/UserConfigStore';

const EVENTS: { value: AppriseTaskNameType; label: string }[] = [
  { value: 'update_subscribed', label: 'A channel check finished' },
  { value: 'extract_download', label: 'Videos were added' },
  { value: 'download_pending', label: 'Downloads finished' },
  { value: 'check_reindex', label: 'Old videos were refreshed' },
];

const SettingsNotifications = () => {
  const { userConfig } = useUserConfigStore();
  const [stored, setStored] = useState<AppriseNotificationType>();
  const [event, setEvent] = useState<AppriseTaskNameType>('download_pending');
  const [url, setUrl] = useState('');
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const reload = useCallback(async () => {
    const { data } = await loadAppriseNotification();
    setStored(data ?? {});
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  const entries = Object.entries(stored ?? {}) as [
    AppriseTaskNameType,
    { urls: string[]; title: string },
  ][];

  return (
    <>
      <title>TA | Notifications</title>
      <div className="boxed-content">
        <SettingsNavigation />
        <Notifications pageName={'all'} />
        <div className="title-bar">
          <h1>Notifications</h1>
          <p className="ux-help">
            Get a message when TA finishes something, through any service{' '}
            <a href="https://github.com/caronc/apprise" target="_blank">
              Apprise
            </a>{' '}
            supports (email, Telegram, ntfy, Discord…).
          </p>
        </div>

        <SettingsSection id="current" title="Sending now">
          {entries.length === 0 && <p className="ux-muted">No notifications are set up.</p>}
          {entries.map(([key, { urls }]) => (
            <div key={key} className="ux-notify-group">
              <h3>{EVENTS.find(item => item.value === key)?.label ?? key}</h3>
              <ul className="ux-plain-list">
                {urls.map(item => (
                  <li key={item}>
                    <code>{item}</code>{' '}
                    <button
                      type="button"
                      className="ux-link-button"
                      aria-label={`Stop sending to ${item}`}
                      onClick={async () => {
                        await deleteAppriseNotificationUrl(key, item);
                        reload();
                      }}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </SettingsSection>

        <SettingsSection id="add" title="Add a notification">
          <form
            className="ux-notify-form"
            onSubmit={async formEvent => {
              formEvent.preventDefault();
              if (!url.trim()) return;
              await createAppriseNotificationUrl(event, url.trim());
              setUrl('');
              setMessage({ text: 'Added.', ok: true });
              reload();
            }}
          >
            <label htmlFor="notify-event">When</label>
            <select
              id="notify-event"
              value={event}
              onChange={changeEvent => setEvent(changeEvent.target.value as AppriseTaskNameType)}
            >
              {EVENTS.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <label htmlFor="notify-url">Send to (Apprise URL)</label>
            <input
              id="notify-url"
              type="text"
              placeholder="e.g. ntfys://ntfy.sh/my-topic"
              value={url}
              onChange={changeEvent => setUrl(changeEvent.target.value)}
            />
            <div className="button-box">
              <button type="submit" disabled={!url.trim()}>
                Add
              </button>
              <button
                type="button"
                disabled={!url.trim()}
                title="Sends one test message to this URL"
                onClick={async () => {
                  setMessage(null);
                  try {
                    const { data } = await testAppriseNotificationUrl(event, url.trim());
                    setMessage({
                      text: data?.message ?? 'Test sent.',
                      ok: Boolean(data?.success),
                    });
                  } catch (error) {
                    setMessage({
                      text: (error as ApiError).message ?? 'The test failed.',
                      ok: false,
                    });
                  }
                }}
              >
                Send a test
              </button>
            </div>
            {message && (
              <p className={message.ok ? 'ux-setting-effect' : 'danger-zone'} role="status">
                {message.text}
              </p>
            )}
          </form>
        </SettingsSection>

        <section className="ux-settings-section">
          <MembershipAppsettings show_help_text={userConfig.show_help_text} />
        </section>
      </div>
      <PaginationDummy />
    </>
  );
};

export default SettingsNotifications;
