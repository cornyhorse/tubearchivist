import { useCallback, useEffect, useState } from 'react';
import loadAppsettingsConfig, { AppSettingsConfigType } from '../api/loader/loadAppsettingsConfig';
import updateAppsettingsConfig from '../api/actions/updateAppsettingsConfig';
import { SettingPath, settingPatch, SettingValue } from '../configuration/settingsFields';

/** the application config, and a save that updates it in place */
const useAppSettings = () => {
  const [config, setConfig] = useState<AppSettingsConfigType>();

  const reload = useCallback(async () => {
    const { data } = await loadAppsettingsConfig();
    if (data) setConfig(data);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
  }, [reload]);

  const save = useCallback(async (path: SettingPath, value: SettingValue) => {
    const response = await updateAppsettingsConfig(settingPatch(path, value));
    if (response.status >= 400) {
      throw { message: response.error?.error ?? `error ${response.status}` };
    }
    const [group, key] = path.split('.') as [keyof AppSettingsConfigType, string];
    setConfig(current =>
      current ? { ...current, [group]: { ...current[group], [key]: value } } : current,
    );
  }, []);

  return { config, save, reload };
};

export default useAppSettings;
