<Terms>
<helmsman-terminology>

hDocs: shorthand for helmsman documents. 

itags: itag stands for issue-tags. issue-tags refer to the tags in the repository git issues page. Helmsman makes heavy use of git issues as a cheap and accessible way to create tickets related to the project. As a result, there are many different tags used to designate which issues mean which thing. For example, issues may be marked as ideas, features, bugs, etc. itag may have shared definitions between projects and repo-unique definitions. The itag section is the ground truth for what each tag means, which tags are used, and when to use each tag.

iticket: an instance of a git issue that has an itag

iPool: a document that contains a variably-structured list of thoughts, ideas, questions, and wants. Typically will live inside a lucidchart. Look at hDocs/remotes#lucidchart section to check for a remote iPool document.


</helmsman-terminology>
<helmsman-version-control>

hVersion: The official version of helmsman currently used in this hProject OR the last official version used in this hProject. hVersion can be checked by looking at .helmsman/hVersion.md which contains the hTag 

hTag: the tag that indicates the hVersion. This matches an official canonical version found in the Helmsman GitHub. The hTag will always be formatted vX.Y.Z and matches a git tag.

canonical version: an official helmsman release tagged on the GitHub. These releases are finished, polished, and documented to offer a standardized starting point for new helmsman projects and a way to compare drift 
in an individual hProject to a baseline.

</helmsman-version-control>
</Terms>

<iTags>
bug: something that is broken or behaving incorrectly. marked with the bug tag on git issues

quickssue: a quick, low-ceremony issue captured from casual input without deep investigation. marked with the quickssue tag on git issues

wonders: an open curiosity or "I wonder if..." question worth capturing for later exploration, not yet an idea or commitment. marked with the wonders tag on git issues
 
</iTags>