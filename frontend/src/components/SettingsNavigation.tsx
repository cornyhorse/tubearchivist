import { NavLink } from 'react-router-dom';
import Routes from '../configuration/routes/RouteList';
import useIsAdmin from '../functions/useIsAdmin';

const ADMIN_PAGES = [
  { to: Routes.SettingsDownload, label: 'What to download' },
  { to: Routes.SettingsWhen, label: 'When' },
  { to: Routes.SettingsMediaServer, label: 'Media server' },
  { to: Routes.SettingsYouTube, label: 'YouTube access' },
  { to: Routes.SettingsNotifications, label: 'Notifications' },
  { to: Routes.SettingsMaintenance, label: 'Maintenance' },
];

const SettingsNavigation = () => {
  const isAdmin = useIsAdmin();

  return (
    <nav className="info-box-item child-page-nav ux-settings-nav" aria-label="Settings">
      <NavLink to={Routes.SettingsDashboard} end>
        <h3>Dashboard</h3>
      </NavLink>
      <NavLink to={Routes.SettingsUser}>
        <h3>User</h3>
      </NavLink>
      {isAdmin &&
        ADMIN_PAGES.map(page => (
          <NavLink key={page.to} to={page.to}>
            <h3>{page.label}</h3>
          </NavLink>
        ))}
    </nav>
  );
};

export default SettingsNavigation;
