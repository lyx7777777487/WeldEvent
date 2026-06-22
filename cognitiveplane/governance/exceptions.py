class ValidationPipelineError(Exception):
    pass


class ValidationShortCircuit(Exception):
    def __init__(self, stage: str, result: str) -> None:
        self.stage = stage
        self.result = result
        super().__init__(f"Validation short-circuited at {stage}: {result}")
