"""Load only packaged, versioned non-sensitive evaluation datasets."""

import json
import re
from importlib.resources import files

from pydantic import ValidationError

from app.modules.evaluation.schemas import EvaluationDataset


class DatasetNotFound(ValueError):
    pass


class DatasetInvalid(ValueError):
    pass


class DatasetLoader:
    def load(self, dataset_id: str) -> EvaluationDataset:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,99}", dataset_id):
            raise DatasetNotFound("Evaluation dataset is not available.")
        path = files("app.modules.evaluation").joinpath("datasets", f"{dataset_id}.json")
        if not path.is_file():
            raise DatasetNotFound("Evaluation dataset is not available.")
        try:
            dataset = EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetInvalid("Evaluation dataset is invalid.") from exc
        if dataset.dataset_id != dataset_id or len({item.id for item in dataset.questions}) != len(dataset.questions):
            raise DatasetInvalid("Evaluation dataset identity or question IDs are invalid.")
        return dataset
