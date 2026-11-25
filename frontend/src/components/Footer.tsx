import { Link } from 'react-router-dom';
import Routes from '../configuration/routes/RouteList';
import { useAuthStore } from '../stores/AuthDataStore';

// the fork's own repository: versions are its fork-vX.Y.Z tags
const FORK_REPO = 'https://github.com/cornyhorse/tubearchivist';
// the upstream release the fork was taken from
const UPSTREAM_BASE = 'v0.5.12';

const Footer = () => {
  const currentYear = new Date().getFullYear();
  const { auth } = useAuthStore();
  const version = auth?.version;
  const taUpdate = auth?.ta_update;

  return (
    <div className="footer">
      <div className="boxed-content">
        <span>© 2021 - {currentYear} </span>
        <span>TubeArchivist </span>
        <span title={`Cornyhorse fork of TubeArchivist ${UPSTREAM_BASE}`}>{version} </span>
        {taUpdate?.version && (
          <>
            <span className="danger-zone">{taUpdate.version} available</span>{' '}
            <span>
              <a href={`${FORK_REPO}/compare/${version}...${taUpdate.version}`} target="_blank">
                What changed
              </a>{' '}
              |{' '}
            </span>
          </>
        )}
        <span>
          <Link to={Routes.About}>About</Link> |{' '}
          <a href={FORK_REPO} target="_blank">
            GitHub
          </a>{' '}
          |{' '}
          <a href={`${FORK_REPO}/pkgs/container/tubearchivist`} target="_blank">
            Image
          </a>{' '}
          |{' '}
          <a href="https://github.com/tubearchivist/tubearchivist" target="_blank">
            Upstream
          </a>
        </span>
      </div>
    </div>
  );
};

export default Footer;
