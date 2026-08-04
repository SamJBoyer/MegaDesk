<helmsman-summary>

This is a Helmsman project.

Helmsman is an idea-to-product development pipeline designed to facilitate collaboration between developers and agents. Helmsman follows an ideate -> prototype -> evaluate -> refactor system that starts with creating robustly structured documents explaining desired-features, questions, internal definitions, multi-project connections, and version-control policies for this project. These documents live inside the hDocs/ folder. 

Helmsman projects have a tag that tells which canonical version of the document they're using. Look in the .helmsman/hVersion.md for this version.

Helmsman projects use a concept called iPool (idea pool) which is a collection of ideas, wants, desires, and implementations that represents a significant amount of the "purpose" for this hProject. The iPool may be stored in the hDocs/tempPool file in the repo, but is most often stored in a lucidchart. If the user asks for anything related to the iPool, first check the hDocs/remotes#lucidchart for instructions for accessing the iPool, then check hDocs/tempPool if no other alternative exists. 

</helmsman-summary>
<repo-structure>

Helmsman Repo Structure: 

.helmsman/
- hVersion.md 

hDocs/
- glossary.md
- master.md
- tempPool.md 
HELMSMAN.md
AGENTS.md

NEVER edit files in the hDocs folder without EXPLICIT permission. 

</repo-structure>
<documents>

Read every document in hDocs 

How to use each document:

<glossary>

Glossary contains the canon definitions used in a project. Glossary, by default, has 3 sections: 
- terms: project-specific dictionary of important terms used in scope. These terms are to be used aggressively in code to develop a shared language between the agent and developer. The terms section of the glossary is the ground truth for such definitions
- itags: itag stands for issue-tags. issue-tags refer to the tag on the repository's git issues page and are used to demarcate types of issues. 
- hLabels: colored emoji squares that demarcate different ideas connected laterally instead of hierarchically. 

do: 
- ask if you’re unsure of terms
- check if an itag already exists before making a new one 
do not:
- ever add new terms or modify term definitions without asking for explicit permission 

</glossary>
<master>

High-level explanation document that contains:
- summary: summary explanation for the purpose of the project 
- remotes: lists remotely-stored resources and how to access them 
- artifacts: work, features, content from abandoned attempts that might dirty the workspace. Previous significant changes stored in a log. This should be used when weird behavior emerges or it’s unclear why an architectural decision was made, as it might be an artifact from a previous version. This document should be used before looking at git history 
- drift: This document explains where the project currently is from the perspective of the developer. This document is an important truth control that bridges what the project should do in the documents and the state of the actual code. Documents are aspirational and upward facing. We always write in our documents what should happen and what should be the case. The code is what’s actually the case. Oftentimes the developer knows that the reality of the code doesn’t match the aspirations of the docs. status is a document to make explicit what the developer actually thinks about the code and what they view as the next steps to convert the reality of the code into the aspirations of the docs
- overlay: Explains which other projects are important to this one and how they interact at a high level 

</master>
<tempPool>

Document contains a loosely structured jot of the user's wants, questions, and wonders. 
wants is a subsection that has a list of things the developer “wants” to happen in this project. It is a jot-down of current desires before they are made actionable. This file serves as a dynamic and changing quick file for the developer to jot down their desires before more work is put in to make it actionable.

</tempPool>

</documents>