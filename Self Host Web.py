import os
import zipfile
import tarfile
from fuzzywuzzy import fuzz
from collections import defaultdict
import threading
import concurrent.futures
import hashlib
import pandas as pd
import mimetypes
import time
import multiprocessing
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

# Global variable to store results
results = None

def fuzzy_match(file_name1, file_name2, threshold):
    return fuzz.ratio(file_name1, file_name2) > threshold

def get_file_info(file_path):
    _, extension = os.path.splitext(file_path)
    file_size = os.path.getsize(file_path)
    mime_type, mime_encoding = mimetypes.guess_type(file_path)
    return extension.lower(), file_size, mime_type, mime_encoding

def is_considered_file(file_path):
    return os.path.isfile(file_path)

def convert_size(size_in_bytes):
    if size_in_bytes > 1000000:
        return f"{size_in_bytes / 1000000:.2f} MB"
    elif size_in_bytes > 1000:
        return f"{size_in_bytes / 1000:.2f} KB"
    else:
        return f"{size_in_bytes} bytes"

def extract_archive(archive_path, extract_folder):
    _, extension = os.path.splitext(archive_path)

    if extension.lower() == '.tar':
        with tarfile.open(archive_path, 'r') as tar:
            tar.extractall(path=extract_folder)
    elif extension.lower() == '.zip':
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)
    elif extension.lower() == '.gz':
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)

def hash_file(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def explore_and_find_duplicates(base_path):
    folder_times_parallel = defaultdict(float)
    folder_times_serial = defaultdict(float)
    unique_file_types = defaultdict(lambda: {'types': set(), 'size': 0})
    all_file_info = {}
    exact_duplicates = defaultdict(set)
    fuzzy_duplicates = defaultdict(set)
    metadata_info = set()
    lock = threading.Lock()

    def process_files(folder_name, file_paths, times_dict):
        local_unique_file_types = {}
        local_metadata_info = set()
        for file_path in file_paths:
            if is_considered_file(file_path):
                start_time = time.time()
                file_type, file_size, mime_type, mime_encoding = get_file_info(file_path)
                if file_path not in local_unique_file_types:
                    local_unique_file_types[file_path] = {'types': set(), 'size': 0}
                local_unique_file_types[file_path]['types'].add(file_type)
                local_unique_file_types[file_path]['size'] += file_size

                all_file_info[file_path] = {'types': set(), 'size': 0}
                all_file_info[file_path]['types'].add(file_type)
                all_file_info[file_path]['size'] += file_size

                local_metadata_info.add((file_path, os.path.basename(file_path), file_type, convert_size(file_size), mime_type, mime_encoding))

                end_time = time.time()
                elapsed_time = end_time - start_time
                times_dict[folder_name] += elapsed_time

        with lock:
            for file_path, file_info in local_unique_file_types.items():
                if file_path not in unique_file_types:
                    unique_file_types[file_path] = {'types': set(), 'size': 0}
                unique_file_types[file_path]['types'].update(file_info['types'])
                unique_file_types[file_path]['size'] += file_info['size']

        with lock:
            metadata_info.update(local_metadata_info)

    def process_folder(folder_path, times_dict):
        nonlocal unique_file_types
        start_time = time.time()
        for root, _, files in os.walk(folder_path):
            file_paths = [os.path.join(root, file) for file in files]
            process_files(os.path.basename(root), file_paths, times_dict)

        end_time = time.time()
        elapsed_time = end_time - start_time
        times_dict[os.path.basename(folder_path)] = elapsed_time

    def explore_compressed_folder(folder_path, times_dict):
        nonlocal unique_file_types
        for file in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file)

            if file.endswith(('.zip', '.tar', '.gz', '.bz2', '.rar', '.7z')):
                st.write(f"Exploring contents of: {file_path}")
                extract_folder = os.path.join(folder_path, os.path.splitext(file)[0])
                extract_archive(file_path, extract_folder)
                process_folder(extract_folder, times_dict)
            else:
                process_folder(file_path, times_dict)

    num_threads = min(multiprocessing.cpu_count() * 2, 32)

    # Parallel processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures_parallel = [
            executor.submit(explore_compressed_folder, base_path, folder_times_parallel),
            executor.submit(process_folder, base_path, folder_times_parallel)
        ]

        for future in concurrent.futures.as_completed(futures_parallel):
            try:
                future.result()
            except Exception as exc:
                print(f"Error: {exc}")

        executor.shutdown(wait=True)

    # Serial processing
    process_folder(base_path, folder_times_serial)

    # Parallelize hashing process using maximum number of processes
    with concurrent.futures.ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        hash_values = list(executor.map(hash_file, all_file_info.keys()))
        all_file_info_hashes = dict(zip(all_file_info.keys(), hash_values))

    processed_files = set()
    for file_path1, info1 in all_file_info.items():
        if file_path1 in processed_files:
            continue
        current_exact_duplicates = set()
        current_fuzzy_duplicates = set()
        for file_path2, info2 in all_file_info.items():
            if (
                file_path1 != file_path2 and
                fuzzy_match(os.path.basename(file_path1), os.path.basename(file_path2), 99) and
                info1['size'] == info2['size']
            ):
                current_exact_duplicates.add(file_path2)
                processed_files.add(file_path2)
            elif (
                file_path1 != file_path2 and
                fuzzy_match(os.path.basename(file_path1), os.path.basename(file_path2), 80) and
                info1['size'] == info2['size']
            ):
                current_fuzzy_duplicates.add(file_path2)
                processed_files.add(file_path2)

        if current_exact_duplicates:
            exact_duplicates[file_path1] = current_exact_duplicates
        if current_fuzzy_duplicates:
            fuzzy_duplicates[file_path1] = current_fuzzy_duplicates

    # Create separate DataFrames for exactly same and slightly similar files
    columns = ['File1', 'File2', 'ContentMatch', 'File1Name', 'File2Name']
    df_exact = pd.DataFrame(columns=columns)
    df_fuzzy = pd.DataFrame(columns=columns)

    for file_path1, duplicate_addresses in exact_duplicates.items():
        for file_path2 in duplicate_addresses:
            hash1 = all_file_info_hashes[file_path1]
            hash2 = all_file_info_hashes[file_path2]
            content_match = hash1 == hash2
            df_exact = pd.concat([df_exact, pd.DataFrame({
                'File1': [file_path1] * len(duplicate_addresses),
                'File2': [file_path2] * len(duplicate_addresses),
                'ContentMatch': [content_match] * len(duplicate_addresses),
                'File1Name': [os.path.basename(file_path1)] * len(duplicate_addresses),
                'File2Name': [os.path.basename(file_path2)] * len(duplicate_addresses),
            })], ignore_index=True)

    for file_path1, duplicate_addresses in fuzzy_duplicates.items():
        for file_path2 in duplicate_addresses:
            hash1 = all_file_info_hashes[file_path1]
            hash2 = all_file_info_hashes[file_path2]
            content_match = hash1 == hash2
            df_fuzzy = pd.concat([df_fuzzy, pd.DataFrame({
                'File1Name': [os.path.basename(file_path1)],
                'File1': [file_path1],
                'File2Name': [os.path.basename(file_path2)],
                'File2': [file_path2],
                'ContentMatch': [content_match],
            })], ignore_index=True)

    # Print metadata information
    metadata_df = pd.DataFrame(metadata_info, columns=['File', 'File Name', 'File Type', 'File Size', 'MIME Type', 'MIME Encoding'])

    # Store results in the global variable
    global results
    results = {
        'df_exact': df_exact,
        'df_fuzzy': df_fuzzy,
        'metadata_df': metadata_df,
        'folders': list(folder_times_parallel.keys()),
        'serial_times': [folder_times_serial[folder] for folder in folder_times_parallel.keys()],
        'parallel_times': [folder_times_parallel[folder] for folder in folder_times_parallel.keys()],
        'index': np.arange(len(folder_times_parallel)),
        'bar_width': 0.35,
        'elapsed_time1': time.time() - start_time1,
        'elapsed_time2': time.time() - start_time2
    }



if __name__ == "__main__":
    st.title("Data Redundancy Removal")
    base_path = st.text_input("Enter the base path:")
    if st.button("Find Duplicates"):
        st.text("Processing... Please wait.")
        start_time1 = time.time()
        start_time2 = time.time()
        explore_and_find_duplicates(base_path)

        # Display results using st.write, st.dataframe, etc.
        st.write("Exactly Same Files (Content Match = True):")
        if results and 'df_exact' in results:
            df_exact_filtered = results['df_exact'][results['df_exact']['ContentMatch'] == True]
            st.dataframe(df_exact_filtered)

        st.write("Slightly Similar Files (Content Match = True):")
        if results and 'df_fuzzy' in results:
            df_fuzzy_filtered = results['df_fuzzy'][results['df_fuzzy']['ContentMatch'] == True]
            st.dataframe(df_fuzzy_filtered)

        st.write("Metadata Information:")
        if results and 'metadata_df' in results:
            st.dataframe(results['metadata_df'])

        st.write("Time Taken for Each Folder:")
        if results and 'folders' in results and 'serial_times' in results and 'parallel_times' in results:
            fig, ax = plt.subplots()
            bar1 = ax.bar(results['index'], results['serial_times'], results['bar_width'], label='Serial Processing')
            bar2 = ax.bar(results['index'] + results['bar_width'], results['parallel_times'], results['bar_width'], label='Parallel Processing')

            ax.set_xlabel('Folders')
            ax.set_ylabel('Time ')
            plt.title(f'Time Taken for Each Folder\nOverall time taken Without Parallel Processing: {results["elapsed_time1"]} seconds -- With Parallel Processing: {results["elapsed_time2"]} seconds')
            ax.set_xticks(results['index'] + results['bar_width'] / 2)
            ax.set_xticklabels(results['folders'], rotation=45, ha='right')
            ax.legend()

            plt.tight_layout()
            st.pyplot(fig)

