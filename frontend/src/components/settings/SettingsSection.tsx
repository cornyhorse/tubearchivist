import { ReactNode } from 'react';
import { AppSettingsConfigType } from '../../api/loader/loadAppsettingsConfig';
import { SettingDef, SettingPath, SettingValue } from '../../configuration/settingsFields';
import SettingField from './SettingField';

type SettingsSectionProps = {
  id?: string;
  title: string;
  intro?: ReactNode;
  fields?: SettingDef[];
  config?: AppSettingsConfigType;
  onSave?: (path: SettingPath, value: SettingValue) => Promise<void>;
  children?: ReactNode;
};

/** a titled group of settings */
const SettingsSection = ({
  id,
  title,
  intro,
  fields,
  config,
  onSave,
  children,
}: SettingsSectionProps) => (
  <section className="ux-settings-section" aria-labelledby={id ? `${id}-title` : undefined}>
    <h2 id={id ? `${id}-title` : undefined}>{title}</h2>
    {intro && <p className="ux-help">{intro}</p>}
    {config &&
      onSave &&
      fields?.map(def => <SettingField key={def.path} def={def} config={config} onSave={onSave} />)}
    {children}
  </section>
);

export default SettingsSection;
