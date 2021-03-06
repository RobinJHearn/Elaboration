#! /usr/bin/python

#
# Scan a set of Ada files and look for circular dependencies
#
# Ada filenames are of the format <PackageName>.1.ada, <PackageName>.2.ada,
# <PackageName>.ads, <PackageName>.adb or <PackageName>.ada
# PackageNames with '.' in them are currently assumed to by sub-packages
# Subpackages get their 'with' clauses added to the parent package
#

import re  # For regular expression usage
import json  # To store data on disk for analysis
import logging  # Handle the output

from collections import deque  # Use a deque for with stack
from pathlib import Path  # To allow easy access to folders and files
from operator import itemgetter  # Allow to sort on element of list

logger = logging.getLogger(__name__)

# Constants
# List of directories to ignore, all files/directories are lower case
IGNORE = [r".git", r".vscode", r"build"]
# List of standard packages and top level packages that may have child packages
STANDARD_PACKAGES = [r"ada", r"system", r"gnat", r"unchecked_deallocation", r"text_io"]


# Regular expression to match Ada files
RE_ADA_FILE = r"(\.[12])?\.ad[abs]$"
# Regular expression to extract the root package name from a filename
RE_ROOT_PACKAGE_NAME = r"^(\w+)[\.\-]?"
# Regular expression to get a full package name from the filename
RE_FULL_PACKAGE_NAME = r"^(.*?)(\.[12])?\.ad[abs]$"
# Regular expression to extract a with'd package name from a source file
RE_WITH_PACKAGE_NAME = r"^\s*with\s+([\w\.*]+)"
# Regular expression to detect the with of a child package
RE_WITH_CHILD_PACKAGE = r"(\w*)\."
# Regular expression to detect a separate statement
RE_SEPARATE = r"^\s*separate\s*\("


def build_with_dictionary(cwd):
    """
    Build a dictionary indexed by Ada package name containing a list of packages
    they 'with'

    Input: cwd => Path object pointing to the directory to parse
    Return: Dictionary object
    """

    # Data used by the local functions
    with_data = {}  # Dictionary populated by the recursive parse_dir function

    def parse_file(f):
        """
        Extract a list of withs from a file, also indicate if it is a sub-unit

        File is read and any with lines processed to extract the with'd package
        Any separate (); statements are detected and flag the file as a sub-unit
        All package names are lower cased

        Input: f => Path object pointing to the file to parse
        Output: tuple (withs_list, is_subunit)
            withs_list => List of with'd packages
            is_subunit => Boolean True if this file is a sub-unit i.e. has a
                          separate statement, otherwise False
        """

        withs_list = []
        is_subunit = False

        with open(f) as file:
            for line in file:
                match = re.search(RE_WITH_PACKAGE_NAME, line.lower())
                if match:
                    with_match = match.group(1)
                    # If this looks like with'ing a child package then output a warning
                    child_match = re.search(RE_WITH_CHILD_PACKAGE, with_match)
                    child_root = ""
                    if child_match:
                        logger.warning(
                            f"+++ Possible child package included in {f.name} ({with_match})"
                        )
                        child_root = child_match.group(1)
                    # Only add the package if it is not already in the list
                    if (
                        not with_match in withs_list
                        and not child_root in STANDARD_PACKAGES
                        and not with_match in STANDARD_PACKAGES
                    ):
                        withs_list.append(with_match)

                # Look for a separate statement
                if re.search(RE_SEPARATE, line.lower()):
                    is_subunit = True
                    break  # If we get to a separate statement then the context has finished

        return withs_list, is_subunit

    def parse_ada_file(f):
        """
        Extract information about the file

        The Ada file will be examined and an entry made/updated in the with_data
        for the package related to the file
        Each with'd package will be added to the list except if it matches the
        source package or is already in the list
        All package names are lower cased

        Input: f => Path object pointing to the file to parse
        Returns: None
        """

        # Got an Ada file so extract the package name from the filename
        package = re.search(RE_FULL_PACKAGE_NAME, f.name.lower()).group(1)
        logger.info(f"--- Parsing file {f.name} Package = {package}")

        withs_list, is_subunit = parse_file(f)
        if is_subunit:
            # For subunits add the withs to the parent package
            package = re.search(RE_ROOT_PACKAGE_NAME, f.stem.lower()).group(1)
        if package in withs_list:
            withs_list.remove(package)

        if package in with_data:
            for w in withs_list:
                if not w in with_data[package]:
                    with_data[package].append(w)
        else:
            with_data[package] = withs_list

    def recursive_parse_dir(cwd):
        """
        Parse a directory of files

        Will recursively enter any sub-directories found unless they match one of the IGNORE directories
        Each Ada file in the directory will be processed
        All package names are lower cased

        Input: cwd => Path object pointing to the directory to parse
        Returns: None
        """

        logger.info(f"--- Parsing directory {cwd.name}")

        for f in cwd.iterdir():
            if f.is_dir():
                # If we get a directory then recurse into it, unless it is to be ignored
                if not f.name.lower() in IGNORE:
                    recursive_parse_dir(f)
            else:
                # Got a file, but is it an Ada file?
                if re.search(RE_ADA_FILE, f.suffix.lower()):
                    parse_ada_file(f)

    # Build the dictionary of packages and what they with
    recursive_parse_dir(cwd)
    logger.info("--- Built dictionary of withs")

    # Remove any entries that have no withs
    delete_list = []
    for key, value in with_data.items():
        if len(value) == 0:
            delete_list.append(key)
    for key in delete_list:
        del with_data[key]
    logger.info("--- Removed empty with lists")

    return with_data


def parse_withs(start, with_data):
    """
        Parse the with_data

        Input: start =>  Start package in the with data
               with_Data => Dictionary of package to with data
        Return: list of circular with stacks
    """

    # deque to hold the current state of with'd units as they are processed
    with_stack = deque()
    circular_stacks = []  # List of circular stacks

    def recursive_parse_withs(start):
        """
        Parse a packages with list recursively

        Input: start => the package to start from
        """

        # Only process the start point if it has withs
        if start in with_data:
            for withs in with_data[start]:
                if withs in with_stack:
                    # This with package is already in the stack,
                    # copy the current stack and add it to the list of circular stacks
                    if withs in with_stack:
                        newStack = deque(with_stack)
                        while newStack.popleft() != withs:
                            pass
                        # Add the current one to make it circular
                        newStack.appendleft(withs)
                        newStack.append(withs)
                        circular_stacks.append(newStack)
                        logger.info(f"--- Circular Stack -> {newStack}")
                        # Don't descend into this with as that way leads to madness
                else:
                    # New with so add it to the stack and recurse into it,
                    # when it returns remove with from stack as it has been processed
                    # Only do this if the with'd package has withs itself
                    if withs in with_data:
                        if len(with_data[withs]) > 0:
                            with_stack.append(withs)
                            logger.debug(f"=== Checking stack -> {with_stack}")
                            recursive_parse_withs(withs)
                            done = with_stack.pop()
                            # Remove completed package to prevent going through it again
                            with_data[done] = []
                            logger.debug(f"=== Removing calls from {done}")

    # Start the stack with the first package
    with_stack.append(start)
    recursive_parse_withs(start)

    return circular_stacks


def scan(cwd):
    """
    Scan a directory of Ada files and locate potential elaboration circularities

    Input: Location to start scanning, used to create a Path object
    Return: A list of circular dependencies
    """
    # Dictionary to hold the lookup of package names and the packages they with
    with_data = {}
    circular_stacks = []  # List of circular stacks

    def is_equal(left, right):
        result = len(left) == len(right)
        if result:
            for l, r in zip(left, right):
                result = l == r
                if not result:
                    break
        return result

    start_dir = Path(cwd)

    logger.info(f"--- Starting directory = {start_dir}")

    with_data = build_with_dictionary(start_dir)

    with open("withs.json", "w") as f:
        json.dump(with_data, f, indent=4, sort_keys=True)

    # Check the with lists to see if it includes any child packages
    for key, value in with_data.items():
        for data in value:
            if re.search(RE_WITH_CHILD_PACKAGE, data):
                logger.warning(f"+++ Child package found {key} includes {data}")
    logger.info("--- Checked for child packages")

    for key, value in with_data.items():
        logger.debug(f"=== {key} => {value}")
    logger.debug("=== End of with dump")

    # Iterate over each package, using it as the start point looking for a loop
    for key in with_data.keys():
        logger.info(f"--- Checking {key} for circularity")
        circular_stacks.extend(parse_withs(key, with_data))
        with_data[key] = []  # Clear entry as it has now been processed
    logger.info("--- Completed checks for circularity")

    with open("raw_circular.json", "w") as f:
        # Convert the list of deque to a list of lists which can be handled by the json package
        temp = []
        for d in circular_stacks:
            temp.append(list(d))
        json.dump(temp, f, indent=4, sort_keys=True)

    # Not sure any of this tidying up is needed, as circular paths are removed when found

    # Tidy up the circularity list
    # Remove stacks that don't start and end with the same package
    # They contain an inner circularity that will be picked up in other stacks
    for cs in circular_stacks:
        if cs[0] != cs[-1]:
            circular_stacks.remove(cs)
    logger.info("--- Tidy circularity lists (part 1)")

    # Remove stacks that are the same circularity
    # This is done by removing the end point (which is the same as the start),
    # the remaining items then form the sequence of withs without returning to a start point.
    # By rotating these and comparing with other stacks identical loops can be removed
    # At the end replace the end item to keep the circularity
    for cs in circular_stacks:
        cs.pop()
    for cs in circular_stacks:
        for ts in circular_stacks:
            if cs == ts:
                continue
            else:
                for _ in range(1, len(ts) + 1):
                    ts.rotate(1)
                    if is_equal(cs, ts):
                        circular_stacks.remove(ts)
                        break
    for cs in circular_stacks:
        fw = cs[0]
        for w in cs:
            if w < fw:
                fw = w
        while cs[0] != fw:
            cs.rotate(1)
    for cs in circular_stacks:
        cs.append(cs[0])
    logger.info("--- Tidy circularity lists (part 2)")

    # Convert to a simple list of lists
    temp = []
    for cs in circular_stacks:
        temp.append(list(cs))

    # Save back as an ordered list of lists
    temp = sorted(temp, key=lambda k: len(k))
    circular_stacks = sorted(temp, key=itemgetter(0))

    # List the circular stacks in order
    for cs in circular_stacks:
        logger.info(f"--- Circular Withs {list(cs)}")

    return circular_stacks


# Main program when run
#
if __name__ == "__main__":
    c = scan(Path.cwd())

    with open("circular.json", "w") as f:
        json.dump(c, f, indent=4, sort_keys=True)

    print(f"Number circular paths found = {len(c)}")
