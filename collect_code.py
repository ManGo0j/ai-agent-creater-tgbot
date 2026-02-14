import os

def collect_project_code(output_filename="full_project_code.txt"):
    # Список папок и файлов, которые нужно игнорировать
    ignore_list = {
        '__pycache__', '.venv', 'venv', '.git', 
        'temp_uploads', 'full_project_code.txt', '.pytest_cache'
    }
    
    # Допустимые расширения файлов
    allowed_extensions = {'.py', '.txt', '.yaml', '.yml', '.sql'}

    with open(output_filename, "w", encoding="utf-8") as outfile:
        for root, dirs, files in os.walk("."):
            # Фильтруем папки (удаляем игнорируемые на месте)
            dirs[:] = [d for d in dirs if d not in ignore_list]

            for file in files:
                if any(file.endswith(ext) for ext in allowed_extensions):
                    file_path = os.path.join(root, file)
                    
                    # Пишем красивый заголовок с путем к файлу
                    outfile.write(f"\n{'='*80}\n")
                    outfile.write(f"FILE: {file_path}\n")
                    outfile.write(f"{'='*80}\n\n")

                    try:
                        with open(file_path, "r", encoding="utf-8") as infile:
                            outfile.write(infile.read())
                    except Exception as e:
                        outfile.write(f"Ошибка при чтении файла: {e}")
                    
                    outfile.write("\n\n")

    print(f"✅ Весь код успешно собран в файл: {output_filename}")

if __name__ == "__main__":
    collect_project_code()