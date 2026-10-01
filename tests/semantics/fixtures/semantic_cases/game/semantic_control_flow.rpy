default semantic_score = 1
default semantic_counter = 0
default semantic_seen = []
default persistent.semantic_initialized = False

label start:
    call semantic_start
    call semantic_call_start
    call semantic_state_start
    call semantic_input_start
    call semantic_screen_start
    call semantic_translation_start
    return

label semantic_start:
    $ persistent.semantic_initialized = True
    $ phase0_checkpoint = True
    "control start"
    if semantic_score >= 1:
        "if true"
    elif semantic_score < 0:
        "if false"
    else:
        "if else"
    while semantic_counter < 2:
        "loop [semantic_counter]"
        $ semantic_counter += 1
    menu semantic_menu:
        "Choose a route."
        "Alpha" if semantic_score >= 1:
            $ semantic_seen.append("Alpha")
            jump semantic_end
        "Hidden" if semantic_score < 0:
            "unreachable"
        "Beta":
            $ semantic_seen.append("Beta")
            call semantic_call("from beta")

label semantic_end:
    "control end [semantic_seen]"
    return
