screen semantic_screen():
    frame:
        vbox:
            text "Fixture screen"
            if persistent.semantic_screen_seen:
                textbutton "Jump" action Jump("semantic_screen_target")
            else:
                textbutton "Return" action Return()

label semantic_screen_start:
    $ persistent.semantic_screen_seen = False
    call screen semantic_screen
    "returned from screen"
    return

label semantic_screen_target:
    "screen jump target"
    return
