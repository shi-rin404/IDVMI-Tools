def formatFilePath(file_path:str, file_extension:str) -> str:
    file_path = reverseString(file_path)
    file_path = file_path.replace(reverseString(f".{file_extension}"), "")
    file_path = reverseString(file_path)
    file_path = f"{file_path}.{file_extension}"    
    
    return file_path

def reverseString(text:str) -> str:
    reversed_text = [text[int(f"-{n+1}")] for n in range(len(text))]
    return "".join(reversed_text)