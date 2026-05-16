"""
Counts of method types in the POS file and cross-references with candidates from Visual Studio's CA1822 analysis.

Run on *_Direct_Candidates.csv and *_Cascading_Candidates.csv

Example:
python3 statistics.py --statistics --pos-file ./stereotypes/ShareX-18.0.1.stereotypes.xml --candidates-file ./reference_set/filtered/Files-4.0.24_Direct_Candidates.csv
"""

import argparse
import os
import re
import csv
from pylibsrcml import srcMLArchiveRead

def remove_generics(name: str) -> str:
    """ 
        Removes C# generic type arguments.
        Example: AcquireLockable<TSessionFunctions> -> AcquireLockable.
    """
    return re.sub(r'<.*?>', '', name)

def remove_namespace(name: str, remove_all: bool) -> str:
    """ 
        Removes namespaces by finding the last '.' (C# specific).
        Example: System.Collections.Generic.List -> List.
    """
    separator = "."
    last = name.rfind(separator)
    if last != -1:
        if remove_all:
            return name[last + len(separator):]
    return name

def run_count_query(archive_path: str, xpath: str) -> int:
    """ Helper to open archive, run xpath, and count results. """
    count = 0
    with srcMLArchiveRead(archive_path, string_read_mode="filename") as archive:
        archive.append_transform_xpath(xpath)
        for unit in archive:
            result = archive.unit_apply_transforms(unit)
            if result.get_value() is not None:
                count += result.get_unit_size()
    return count

def calculate_statistics(pos_file, candidates_file_path):
    """ Main statistics generation logic. """
    print("--- Running Statistics ---")
    
    stats_data = {}
    queries = { 
        "Methods": "//src:function[not(ancestor::src:property)]",
        "Instance Methods": "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static')]",
        "Stereotyped Instance Methods": "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static') and @st:stereotype]",
        "Degenerate Instance Methods": "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static') and @*[contains(., 'incidental') or contains(., 'stateless') or contains(., 'empty')]]",
    }

    print(f"Counting method types in {pos_file}...")
    for key, xpath in queries.items():
        stats_data[key] = run_count_query(pos_file, xpath)

    print(f"Cross-referencing with candidates: {candidates_file_path}")
    methods_alone = {}
    candidate_map = {}
    with open(candidates_file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None) # Skip header
        for row in reader:
            name = remove_generics(row[0])
            name = remove_namespace(name, True)      
            key = name + row[1] + row[2] # Name + Line + File
            candidate_map[key] = row

    xpath_names = "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static')]/src:name"
    with srcMLArchiveRead(pos_file, string_read_mode="filename") as archive:
        archive.append_transform_xpath(xpath_names)
        for unit in archive:
            result = archive.unit_apply_transforms(unit)
            if result.get_value() is not None: 
                fname = unit.get_filename()
                n = result.get_unit_size()
                for i in range(n):
                    u = result.get_unit(i)                  
                    xml = u.get_srcml() or ""
                    match = re.search(r'pos:start="(\d+):', xml)
                    line = match.group(1) if match else "0"
                    name = remove_generics(u.unparse_string())
                    name = remove_namespace(name, True)
                    key = name + line + fname
                    if key in candidate_map:
                        methods_alone[key] = candidate_map[key]
        stats_data["Methods That Can be Static (CA1822)"] = len(methods_alone)

    xpath_deg = "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static') and @*[contains(., 'incidental') or contains(., 'stateless') or contains(., 'empty')]]/src:name"
    count_detectable_by_degenerates = 0
    with srcMLArchiveRead(pos_file, string_read_mode="filename") as archive:
        archive.append_transform_xpath(xpath_deg)
        for unit in archive:
            result = archive.unit_apply_transforms(unit)
            if result.get_value() is not None:    
                fname = unit.get_filename()
                n = result.get_unit_size()
                for i in range(n):
                    u = result.get_unit(i)
                    xml = u.get_srcml() or ""
                    match = re.search(r'pos:start="(\d+):', xml)
                    line = match.group(1) if match else "0"   
                    name = remove_generics(u.unparse_string())
                    name = remove_namespace(name, True)
                    key = name + line + fname
                    if key in methods_alone:
                        count_detectable_by_degenerates += 1
    stats_data["Methods That Can be Static (CA1822 Detectable by Degenerates)"] = count_detectable_by_degenerates

    count_detectable_by_stereotypes = 0
    xpath_st = "//src:function[not(ancestor::src:property) and not(src:type/src:specifier='static') and @st:stereotype]/src:name"    
    with srcMLArchiveRead(pos_file, string_read_mode="filename") as archive:
        archive.append_transform_xpath(xpath_st)
        for unit in archive:
            result = archive.unit_apply_transforms(unit)
            if result.get_value() is not None:    
                fname = unit.get_filename()
                n = result.get_unit_size()
                for i in range(n):
                    u = result.get_unit(i)                    
                    # Extract Line
                    xml = u.get_srcml() or ""
                    match = re.search(r'pos:start="(\d+):', xml)
                    line = match.group(1) if match else "0"                    
                    # Extract Name
                    name = remove_generics(u.unparse_string())
                    name = remove_namespace(name, True)
                    key = name + line + fname
                    if key in methods_alone:
                        count_detectable_by_stereotypes += 1
    stats_data["Methods That Can be Static (CA1822 Detectable by Stereotypes)"] = count_detectable_by_stereotypes

    print("\n--- Statistics Summary ---")
    for k, v in stats_data.items():
        print(f"{k}: {v}")


def main():
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--statistics", action="store_true", help="Requires --pos-file and --candidates-file")
    parser.add_argument("--pos-file", help="srcML archive with stereotypes and positions (XML format)")
    parser.add_argument("--candidates-file", help="Static method candidate file generated by Visual Studio (CSV format)")
    
    args = parser.parse_args()
    
    if args.statistics:
        if not args.pos_file or not args.candidates_file:
            parser.error("The --statistics flag requires --pos-file and --candidates-file to be specified.")

        if not os.path.exists(args.pos_file):
            parser.error(f"Error: file not found: {args.pos_file}")
        
        if not os.path.exists(args.candidates_file):
            parser.error(f"Error: file not found: {args.candidates_file}")

        # Call stats with explicit paths
        calculate_statistics(
            pos_file=args.pos_file, 
            candidates_file_path=args.candidates_file
        )
    
 
if __name__ == "__main__":
    main()
