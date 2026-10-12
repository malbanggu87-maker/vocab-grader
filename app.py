# Incorrect (causes IndentationError)
for item in parsed_data.details:
print(item)  # Not indented!

# Correct
for item in parsed_data.details:
    print(item)  # Properly indented (4 spaces or 1 tab)
