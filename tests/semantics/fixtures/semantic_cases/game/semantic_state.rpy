default semantic_value = 2
default semantic_items = [1]
default persistent.semantic_flag = False

label semantic_state_start:
    $ semantic_value += 3
    $ semantic_items[0] += semantic_value
    $ persistent.semantic_flag = True
    "state [semantic_value] [semantic_items[0]] [persistent.semantic_flag]"
    return
