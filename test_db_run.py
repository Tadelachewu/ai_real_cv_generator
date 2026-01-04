from db import save_user_data, load_user_data

print('Saving...', save_user_data(123, {'name':'Alice','ts': 'now'}))
print('Loaded:', load_user_data(123))
