label semantic_input_start:
    $ semantic_random = renpy.random.randint(1, 3)
    $ semantic_name = renpy.input("Fixture name", default="Default")
    "random [semantic_random]"
    "input [semantic_name]"
    return
