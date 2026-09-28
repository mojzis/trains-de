from python_template.greeting import greet


def test_greet_addresses_the_name():
    assert greet("world") == "Hello, world!"
