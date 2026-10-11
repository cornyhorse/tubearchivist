import { useEffect, useState } from 'react';
import Notifications from '../components/Notifications';
import PaginationDummy from '../components/PaginationDummy';
import SettingsNavigation from '../components/SettingsNavigation';
import SettingsSection from '../components/settings/SettingsSection';
import DownloadRuleForm from '../components/DownloadRuleForm';
import loadDefaultDownloadRule from '../api/loader/loadDownloadRule';
import { DownloadRuleType } from '../api/actions/updateDownloadRule';
import useAppSettings from '../functions/useAppSettings';
import { summarizeRule } from '../functions/describeRule';
import {
  CHECK_FIELDS,
  COMMENT_FIELDS,
  EXTRA_FIELDS,
  FORMAT_FIELDS,
  SPEED_FIELDS,
  SUBTITLE_FIELDS,
} from '../configuration/settingsFields';

const SettingsDownload = () => {
  const { config, save } = useAppSettings();
  const [defaultRule, setDefaultRule] = useState<DownloadRuleType | null>();
  const [ruleNonce, setRuleNonce] = useState(0);

  useEffect(() => {
    (async () => {
      const { data } = await loadDefaultDownloadRule();
      setDefaultRule(data ?? null);
    })();
  }, [ruleNonce]);

  return (
    <>
      <title>TA | What to download</title>
      <div className="boxed-content">
        <SettingsNavigation />
        <Notifications pageName={'all'} />
        <div className="title-bar">
          <h1>What to download</h1>
          <p className="ux-help">
            Which videos TA downloads when it checks your channels, and in what quality.
          </p>
        </div>

        <SettingsSection
          id="default-rule"
          title="Default rule"
          intro="Applies to every channel and playlist without its own rule. Empty fields match everything. Videos you add by link are always downloaded."
        >
          {defaultRule !== undefined && (
            <p className="ux-setting-effect">
              Now:{' '}
              {defaultRule && defaultRule.enabled
                ? summarizeRule(defaultRule).label
                : 'no default rule, every video found is downloaded'}
              .
            </p>
          )}
          {defaultRule !== undefined && (
            <DownloadRuleForm
              key={JSON.stringify(defaultRule)}
              rule={defaultRule}
              onSaved={() => setRuleNonce(current => current + 1)}
            />
          )}
        </SettingsSection>

        <SettingsSection
          id="checks"
          title="Channel checks"
          intro="How far back each check of a channel or playlist looks."
          fields={CHECK_FIELDS}
          config={config}
          onSave={save}
        />
        <SettingsSection
          id="format"
          title="Format and quality"
          fields={FORMAT_FIELDS}
          config={config}
          onSave={save}
        />
        <SettingsSection
          id="subtitles"
          title="Subtitles"
          fields={SUBTITLE_FIELDS}
          config={config}
          onSave={save}
        />
        <SettingsSection
          id="comments"
          title="Comments"
          fields={COMMENT_FIELDS}
          config={config}
          onSave={save}
        />
        <SettingsSection
          id="extras"
          title="Extras"
          fields={EXTRA_FIELDS}
          config={config}
          onSave={save}
        />
        <SettingsSection
          id="speed"
          title="Speed and clean-up"
          fields={SPEED_FIELDS}
          config={config}
          onSave={save}
        />
      </div>
      <PaginationDummy />
    </>
  );
};

export default SettingsDownload;
