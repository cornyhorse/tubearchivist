import { Link, useNavigate } from 'react-router-dom';
import iconSearch from '/img/icon-search.svg';
import iconGear from '/img/icon-gear.svg';
import iconExit from '/img/icon-exit.svg';
import Routes from '../configuration/routes/RouteList';
import NavigationItem from './NavigationItem';
import logOut from '../api/actions/logOut';
import useIsAdmin from '../functions/useIsAdmin';
import ThemePicker from './ThemePicker';

const Navigation = () => {
  const isAdmin = useIsAdmin();
  const navigate = useNavigate();
  const handleLogout = async (event: { preventDefault: () => void }) => {
    event.preventDefault();
    await logOut();
    navigate(Routes.Login);
  };

  return (
    <div className="boxed-content">
      <div className="top-nav">
        <Link to={Routes.Home} className="nav-logo">
          <div></div>
        </Link>
        <div className="nav-items">
          <div className="nav-item-group">
            <NavigationItem label="home" navigateTo={Routes.Home} />
            <NavigationItem label="channels" navigateTo={Routes.Channels} />
          </div>
          <div className="nav-item-group">
            <NavigationItem label="playlists" navigateTo={Routes.Playlists} />
            {isAdmin && <NavigationItem label="downloads" navigateTo={Routes.Downloads} />}
          </div>
        </div>
        <div className="nav-icons">
          <ThemePicker />
          <Link to={Routes.Search} title="Search videos, channels and playlists">
            <img src={iconSearch} alt="Search" />
          </Link>
          <Link to={Routes.SettingsDashboard} title="Settings">
            <img src={iconGear} alt="Settings" />
          </Link>
          <button type="button" className="ux-icon-only" title="Log out" onClick={handleLogout}>
            <img className="alert-hover" src={iconExit} alt="Log out" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default Navigation;
