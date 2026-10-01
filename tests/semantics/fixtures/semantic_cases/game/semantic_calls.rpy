label semantic_call(message, suffix="default"):
    "call [message] [suffix]"
    return message + " returned"

label semantic_call_start:
    $ semantic_call_result = "unset"
    call semantic_call("actual", suffix="keyword")
    $ semantic_call_result = _return
    "call result [semantic_call_result]"
    return
