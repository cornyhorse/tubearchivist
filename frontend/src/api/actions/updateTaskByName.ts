import APIClient from '../../functions/APIClient';

type TaskNamesType = 'download_pending' | 'update_subscribed' | 'resync_metadata' | 'emby_sync';

const updateTaskByName = async (taskName: TaskNamesType) => {
  return APIClient(`/api/task/by-name/${taskName}/`, {
    method: 'POST',
  });
};

export default updateTaskByName;
