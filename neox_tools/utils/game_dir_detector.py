import os

main_dir = os.path.join('C:\\', 'IdentityV2', 'res')
author_dir = os.path.join('S:\\', '3dmigoto', 'Neox2', 'dwrg', 'res')

def check_game_directory():
    if os.path.exists(main_dir):
        if not os.path.exists(os.path.join(main_dir, 'mod')):
            os.mkdir(os.path.join(main_dir, 'mod'))
        return os.path.join(main_dir, 'mod')
    elif os.path.exists(author_dir):
        if not os.path.exists(os.path.join(author_dir, 'mod')):
            os.mkdir(os.path.join(author_dir, 'mod'))
        return os.path.join(author_dir, 'mod')
    return ""