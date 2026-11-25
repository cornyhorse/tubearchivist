import { useState } from 'react';
import { Link } from 'react-router-dom';
import Routes from '../configuration/routes/RouteList';
import { createJob } from '../api/ux/client';
import Button from './Button';

type CheckSourceButtonProps = {
  kind: 'channel' | 'playlist';
  id: string;
  name: string;
};

/**
 * look for new videos of this one channel or playlist now, instead of
 * checking every subscription; downloads follow the item's auto-download
 */
const CheckSourceButton = ({ kind, id, name }: CheckSourceButtonProps) => {
  const [message, setMessage] = useState<string | null>(null);

  const check = async () => {
    const { data, error } = await createJob({ type: `check_${kind}`, target_id: id });
    if (error || !data) {
      setMessage('Could not queue the check.');
      return;
    }
    setMessage(data.merged ? 'A check was already waiting; moved it up.' : 'Check queued.');
  };

  if (message) {
    return (
      <p className="check-source-message">
        {message} Follow it on the <Link to={Routes.Queue}>Queue page</Link>.
      </p>
    );
  }

  return (
    <Button
      label="Check for new videos"
      type="button"
      title={`Look for new videos of ${name} on YouTube now (this ${kind} only)`}
      onClick={check}
    />
  );
};

export default CheckSourceButton;
