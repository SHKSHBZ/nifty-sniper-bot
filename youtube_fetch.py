import subprocess
import json
import codecs

try:
    # Run the CLI
    output = subprocess.check_output(['python', '-m', 'youtube_transcript_api', 'T3f3cm1MlgI', '--languages', 'hi'])
    
    # The default output is a string representation of a Python list
    # We can evaluate it safely using ast.literal_eval
    import ast
    data = ast.literal_eval(output.decode('utf-8', errors='ignore'))
    
    # If the output is a dictionary (for multiple video IDs), extract the list
    if isinstance(data, dict) and 'T3f3cm1MlgI' in data:
        data = data['T3f3cm1MlgI']
        
    # Write the texts to a file
    with codecs.open('yt_hi.txt', 'w', 'utf-8') as f:
        for item in data:
            f.write(item['text'] + '\n')
    print("Success")
except Exception as e:
    print(f"Error: {e}")
