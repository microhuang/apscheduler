"""
Stores jobs in a MongoDB database.
"""
from __future__ import absolute_import

import six

from apscheduler.jobstores.base import BaseJobStore, JobLookupError, ConflictingIdError
from apscheduler.util import maybe_ref, datetime_to_utc_timestamp, utc_timestamp_to_datetime
from apscheduler.job import Job

try:
    import cPickle as pickle
except ImportError:  # pragma: nocover
    import pickle

try:
    from bson.binary import Binary
    from pymongo.errors import DuplicateKeyError
    from pymongo import Connection, ASCENDING
except ImportError:  # pragma: nocover
    raise ImportError('MongoDBJobStore requires PyMongo installed')


class MongoDBJobStore(BaseJobStore):
    def __init__(self, database='apscheduler', collection='jobs', connection=None,
                 pickle_protocol=pickle.HIGHEST_PROTOCOL, **connect_args):
        super(MongoDBJobStore, self).__init__()
        self.pickle_protocol = pickle_protocol

        if not database:
            raise ValueError('The "database" parameter must not be empty')
        if not collection:
            raise ValueError('The "collection" parameter must not be empty')

        if connection:
            self.connection = maybe_ref(connection)
        else:
            connect_args.setdefault('w', 1)
            self.connection = Connection(**connect_args)

        self.collection = self.connection[database][collection]
        self.collection.ensure_index('next_run_time', sparse=True)

    def lookup_job(self, job_id):
        document = self.collection.find_one(job_id, ['job_state'])
        if document is None:
            raise JobLookupError(job_id)
        return self._reconstitute_job(document['job_state'])

    def get_pending_jobs(self, now):
        timestamp = datetime_to_utc_timestamp(now)
        return self._get_jobs({'next_run_time': {'$lte': timestamp}})

    def get_next_run_time(self):
        document = self.collection.find_one({'next_run_time': {'$ne': None}}, fields=['next_run_time'],
                                            sort=[('next_run_time', ASCENDING)])
        return utc_timestamp_to_datetime(document['next_run_time']) if document else None

    def get_all_jobs(self):
        return self._get_jobs({})

    def add_job(self, job):
        try:
            self.collection.insert({
                '_id': job.id,
                'next_run_time': datetime_to_utc_timestamp(job.next_run_time),
                'job_state': Binary(pickle.dumps(job.__getstate__(), self.pickle_protocol))
            })
        except DuplicateKeyError:
            raise ConflictingIdError(job.id)

    def update_job(self, job):
        changes = {
            'next_run_time': datetime_to_utc_timestamp(job.next_run_time),
            'job_state': Binary(pickle.dumps(job.__getstate__(), self.pickle_protocol))
        }
        result = self.collection.update({'_id': job.id}, {'$set': changes})
        if result and result['n'] == 0:
            raise JobLookupError(id)

    def remove_job(self, job_id):
        result = self.collection.remove(job_id)
        if result and result['n'] == 0:
            raise JobLookupError(job_id)

    def remove_all_jobs(self):
        self.collection.remove()

    def shutdown(self):
        self.connection.disconnect()

    @staticmethod
    def _reconstitute_job(job_state):
        job_state = pickle.loads(job_state)
        job = Job.__new__(Job)
        job.__setstate__(job_state)
        return job

    def _get_jobs(self, conditions):
        jobs = []
        for document in self.collection.find(conditions, ['_id', 'job_state'], sort=[('next_run_time', ASCENDING)]):
            try:
                jobs.append(self._reconstitute_job(document['job_state']))
            except:
                self._logger.exception(six.u('Unable to restore job (id=%s)'), document['_id'])

        return jobs

    def __repr__(self):
        return '<%s (connection=%s)>' % (self.__class__.__name__, self.connection)
