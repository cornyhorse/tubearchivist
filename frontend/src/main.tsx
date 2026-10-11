import * as React from 'react';
import * as ReactDOM from 'react-dom/client';
import { createBrowserRouter, redirect, RouterProvider } from 'react-router-dom';
import Routes from './configuration/routes/RouteList';
import './themes.css';
import './style.css';
import './ux.css';
import Base from './pages/Base';
import About from './pages/About';
import Channels from './pages/Channels';
import ErrorPage from './pages/ErrorPage';
import Home from './pages/Home';
import Playlist from './pages/Playlist';
import Playlists from './pages/Playlists';
import Search from './pages/Search';
import SettingsDashboard from './pages/SettingsDashboard';
import Video from './pages/Video';
import Login from './pages/Login';
import SettingsMaintenance from './pages/SettingsMaintenance';
import SettingsDownload from './pages/SettingsDownload';
import SettingsWhen from './pages/SettingsWhen';
import SettingsMediaServer from './pages/SettingsMediaServer';
import SettingsYouTube from './pages/SettingsYouTube';
import SettingsNotifications from './pages/SettingsNotifications';
import SettingsUser from './pages/SettingsUser';
import loadUserMeConfig from './api/loader/loadUserConfig';
import loadAuth from './api/loader/loadAuth';
import ChannelBase from './pages/ChannelBase';
import ChannelVideo from './pages/ChannelVideo';
import ChannelPlaylist from './pages/ChannelPlaylist';
import ChannelAbout from './pages/ChannelAbout';
import FoundVideos from './pages/FoundVideos';
import Queue from './pages/Queue';
import Rules from './pages/Rules';
import loadUserAccount from './api/loader/loadUserAccount';
import loadAppsettingsConfig from './api/loader/loadAppsettingsConfig';
import NotFound from './pages/NotFound';

const router = createBrowserRouter(
  [
    {
      path: Routes.Home,
      loader: async () => {
        console.log('------------ after reload');

        let authData = null;

        try {
          const auth = await loadAuth();
          if (auth.status === 403) {
            return redirect(Routes.Login);
          }

          authData = await auth.json();
        } catch (error) {
          console.log(error);

          return redirect(Routes.Login);
        }

        const { data: userConfig } = await loadUserMeConfig();
        const { data: userAccount } = await loadUserAccount();
        const { data: appSettings } = await loadAppsettingsConfig();

        return { userConfig, userAccount, appSettings, auth: authData };
      },
      element: <Base />,
      errorElement: <ErrorPage />,
      children: [
        {
          index: true,
          element: <Home />,
        },
        {
          path: Routes.Video(':videoId'),
          element: <Video />,
        },
        {
          path: Routes.Channels,
          element: <Channels />,
        },
        {
          path: Routes.Channel(':channelId'),
          element: <ChannelBase />,
          children: [
            {
              index: true,
              path: Routes.ChannelVideo(':channelId'),
              element: <ChannelVideo videoType="videos" />,
            },
            {
              path: Routes.ChannelStream(':channelId'),
              element: <ChannelVideo videoType="streams" />,
            },
            {
              path: Routes.ChannelShorts(':channelId'),
              element: <ChannelVideo videoType="shorts" />,
            },
            {
              path: Routes.ChannelPlaylist(':channelId'),
              element: <ChannelPlaylist />,
            },
            {
              path: Routes.ChannelAbout(':channelId'),
              element: <ChannelAbout />,
            },
          ],
        },
        {
          path: Routes.Playlists,
          element: <Playlists />,
        },
        {
          path: Routes.Playlist(':playlistId'),
          element: <Playlist />,
        },
        {
          path: Routes.Downloads,
          element: <FoundVideos />,
        },
        {
          path: Routes.Queue,
          element: <Queue />,
        },
        {
          path: Routes.Rules,
          element: <Rules />,
        },
        {
          path: Routes.Search,
          element: <Search />,
        },
        {
          path: Routes.SettingsDashboard,
          element: <SettingsDashboard />,
        },
        {
          path: Routes.SettingsDownload,
          element: <SettingsDownload />,
        },
        {
          path: Routes.SettingsWhen,
          element: <SettingsWhen />,
        },
        {
          path: Routes.SettingsMediaServer,
          element: <SettingsMediaServer />,
        },
        {
          path: Routes.SettingsYouTube,
          element: <SettingsYouTube />,
        },
        {
          path: Routes.SettingsNotifications,
          element: <SettingsNotifications />,
        },
        {
          path: Routes.SettingsMaintenance,
          element: <SettingsMaintenance />,
        },
        {
          path: Routes.SettingsApplication,
          loader: () => redirect(Routes.SettingsDownload),
        },
        {
          path: Routes.SettingsScheduling,
          loader: () => redirect(Routes.SettingsWhen),
        },
        {
          path: Routes.SettingsActions,
          loader: () => redirect(Routes.SettingsMaintenance),
        },
        {
          path: Routes.SettingsUser,
          element: <SettingsUser />,
        },
        {
          path: Routes.About,
          element: <About />,
        },
      ],
    },
    {
      path: Routes.Login,
      element: <Login />,
      errorElement: <ErrorPage />,
    },
    {
      path: '*',
      element: <NotFound />,
      errorElement: <ErrorPage />,
    },
  ],
  { basename: import.meta.env.BASE_URL },
);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
