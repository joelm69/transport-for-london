"""Data-lake storage targets with create-only writes.

LocalLake mirrors the bucket layout under a local folder (development).
GcsLake writes to Google Cloud Storage. Both refuse to overwrite existing
objects, so a re-run can never silently replace history.
"""

from pathlib import Path


class ObjectExistsError(Exception):
    pass


class LocalLake:
    def __init__(self, root):
        self.root = Path(root)

    def __repr__(self):
        return f"LocalLake({str(self.root)!r})"

    def uri(self, path):
        return str(self.root / path)

    def write_new(self, path, data, content_type=None):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as file:  # "x": fail if it exists
                file.write(data)
        except FileExistsError:
            raise ObjectExistsError(self.uri(path)) from None

    def read(self, path):
        return (self.root / path).read_bytes()


class GcsLake:
    def __init__(self, bucket_name, project=None, client=None):
        if client is None:
            from google.cloud import storage
            client = storage.Client(project=project)
        self.bucket_name = bucket_name
        self._bucket = client.bucket(bucket_name)

    def __repr__(self):
        return f"GcsLake({self.bucket_name!r})"

    def uri(self, path):
        return f"gs://{self.bucket_name}/{path}"

    def write_new(self, path, data, content_type=None):
        from google.api_core.exceptions import PreconditionFailed

        blob = self._bucket.blob(path)
        try:
            # if_generation_match=0: succeed only if the object does not exist.
            blob.upload_from_string(
                data, content_type=content_type, if_generation_match=0
            )
        except PreconditionFailed:
            raise ObjectExistsError(self.uri(path)) from None

    def read(self, path):
        return self._bucket.blob(path).download_as_bytes()
