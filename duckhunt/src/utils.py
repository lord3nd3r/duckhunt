def parse_message(line):
    prefix = ''
    trailing = ''
    if line.startswith(':'):
        prefix, line = line[1:].split(' ', 1)
    if ' :' in line:
        line, trailing = line.split(' :', 1)
    parts = line.split()
    command = parts[0] if parts else ''
    params = parts[1:] if len(parts) > 1 else []
    return prefix, command, params, trailing
