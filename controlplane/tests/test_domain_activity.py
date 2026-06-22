from controlplane.domain.activity import ActivityStatus, ActivityInput, ActivityOutput


def test_activity_status_values():
    assert ActivityStatus.OK.value == "OK"
    assert ActivityStatus.MARGINAL.value == "MARGINAL"
    assert ActivityStatus.NG.value == "NG"
    assert ActivityStatus.ERROR.value == "ERROR"


def test_activity_input_creation():
    inp = ActivityInput(
        control_point_id="CP0",
        workflow_context={"template_id": "t1"},
        params={"key": "val"},
    )
    assert inp.control_point_id == "CP0"
    assert inp.params["key"] == "val"


def test_activity_input_default_params():
    inp = ActivityInput(control_point_id="CP0", workflow_context={})
    assert inp.params is None


def test_activity_output_creation():
    out = ActivityOutput(status=ActivityStatus.OK, data={"result": 42})
    assert out.status == ActivityStatus.OK
    assert out.data["result"] == 42
    assert out.error is None


def test_activity_output_error():
    out = ActivityOutput(status=ActivityStatus.ERROR, error="timeout")
    assert out.status == ActivityStatus.ERROR
    assert out.data is None
    assert out.error == "timeout"
