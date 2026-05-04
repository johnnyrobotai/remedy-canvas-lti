"""Joshua Design System HTML templates for Canvas courses."""

from string import Template

from lti_app.models import TemplateType


class JoshuaTemplates:
    """ADA-compliant HTML templates following the Joshua Design System."""

    # Template 1: General Content Page
    GENERAL_CONTENT = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${title}</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
${content_sections}
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <p style="text-align: right;"><em>Click on the Next button below to continue.</em> <span aria-hidden="true">▼</span></p>
        </div>
    </div>
</div>''')

    # Section template for general content
    CONTENT_SECTION = Template('''
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>${heading}</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
${body}
            </div>''')

    # Template 2: Front Page
    FRONT_PAGE = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${course_title}</strong></h2>
${banner_image}
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Class Information</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p><strong>Location:</strong> ${location}<br/>
                <strong>Meeting Days and Time:</strong> ${meeting_time}<br/>
                <strong>Instructor:</strong> ${instructor_name}<br/>
                <strong>In-Person Office Hours:</strong> ${office_hours}<br/>
                <strong>Office Location:</strong> ${office_location}<br/>
                <strong>Online Office Hours:</strong> ${online_hours}<br/>
                <strong>Zoom Link:</strong> ${zoom_link}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Course Overview</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>Welcome to ${course_name}!</p>
                <p>${course_description}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Student Learning Outcomes</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ol style="list-style-type: decimal;">
${slo_items}
                </ol>
            </div>
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <h3 style="text-align: center;"><strong>Course Navigation</strong></h3>
            <p style="text-align: center;"><span>To access course information and resources, select <strong>Modules</strong> on the left-hand menu.</span></p>
        </div>
    </div>
</div>''')

    # Template 3: Canvas Announcement
    ANNOUNCEMENT = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${title}</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
${content_sections}
        </div>
    </div>
</div>''')

    # Template 4: Meet Your Instructor
    MEET_INSTRUCTOR = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>Hello, my name is ${instructor_name}!</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
${instructor_image}
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Quick Introduction</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${introduction}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>3 Fun Facts About Me</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ol>
${fun_facts}
                </ol>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Hours</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p><strong>In-Person Office Hours:</strong> ${office_hours}<br/>
                <strong>Office Location:</strong> ${office_location}<br/>
                <strong>Online Office Hours:</strong> ${online_hours}<br/>
                <strong>Zoom Link:</strong> ${zoom_link}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Contact Information</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <h4><strong><span style="color: ${primary};">The best way to contact me:</span></strong></h4>
                <p>${contact_method}</p>
                <h4><strong><span style="color: ${primary};">Response Time</span></strong></h4>
                <p>I will respond to you within ${response_time}.</p>
            </div>
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <p style="text-align: right;"><em>Click on the Next button below to continue.</em> <span aria-hidden="true">▼</span></p>
        </div>
    </div>
</div>''')

    # Template 5: Module Overview
    MODULE_OVERVIEW = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${topic_name}</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Overview</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${overview}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Objectives</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>By the end of this week, you will be able to:</p>
                <ul>
${objectives}
                </ul>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Agenda for the Week</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <h4><strong><span style="color: ${primary};">Read:</span></strong></h4>
                <ul>
${readings}
                </ul>
                <h4><strong><span style="color: ${primary};">Watch:</span></strong></h4>
                <ul>
${videos}
                </ul>
                <h4><strong><span style="color: ${primary};">Complete the following assignments:</span></strong></h4>
                <ul>
${assignments}
                </ul>
            </div>
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <p style="text-align: right;"><em>Click on the Next button below to continue.</em> <span aria-hidden="true">▼</span></p>
        </div>
    </div>
</div>''')

    # Template 6: Assignment Instructions
    ASSIGNMENT = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${assignment_title}</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Assignment Description and Instructions</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${description}</p>
                <p>${instructions}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Technical Requirements</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ul>
                    <li><strong>Due Date:</strong> ${due_date}</li>
                    <li><strong>Page Length/Word Count Requirement:</strong> ${length_requirement}</li>
                    <li><strong>Formatting Requirement:</strong> ${formatting}</li>
                </ul>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Grading</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${grading_info}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Canvas Support</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ul>
                    <li><a href="https://guides.instructure.com/m/4212/l/54352-how-do-i-view-the-rubric-for-my-assignment">How do I view the rubric for my assignment?</a></li>
                    <li><a href="https://guides.instructure.com/m/4212/l/41972-how-do-i-submit-an-online-assignment">How do I submit an online assignment?</a></li>
                    <li><a href="https://guides.instructure.com/m/4212/l/54358-how-do-i-know-when-my-instructor-has-graded-my-assignment">How do I know when my instructor has graded my assignment?</a></li>
                </ul>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Directions to Submit</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ol>
                    <li>On the right hand Menu, you will see a <strong>START ASSIGNMENT</strong> button with a white plus sign.</li>
                    <li>Click on the <strong>SUBMIT ASSIGNMENT</strong> button. Then click <strong>Choose a file to upload</strong> to look for your file on your computer.</li>
                    <li>When done, click the <strong>SUBMIT ASSIGNMENT</strong> button.</li>
                </ol>
            </div>
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <p style="text-align: right;"><em>Click on the Next button below to continue.</em> <span aria-hidden="true">▼</span></p>
        </div>
    </div>
</div>''')

    # Template 7: Discussion Board
    DISCUSSION = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>${discussion_title}</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Topic Overview</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${topic_overview}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Discussion Questions</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ol>
${questions}
                </ol>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Technical Support</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>Need help using Canvas Discussions? If so, please review the following Canvas guide pages:</p>
                <ul>
                    <li><a href="https://community.canvaslms.com/docs/DOC-10561-4212190706" target="_blank" rel="noopener">How to reply to a discussion as a student</a></li>
                    <li><a href="https://community.canvaslms.com/docs/DOC-10701-canvas-student-guide-table-of-contents#jive_content_id_Discussions" target="_blank" rel="noopener">Canvas Student Guide for Discussions</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-upload-a-video-using-the-Rich-Content-Editor-as-a/ta-p/429" target="_blank" rel="noopener">How to upload a video to a discussion</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-embed-an-image-in-a-discussion-reply-as-a-student/ta-p/313" target="_blank" rel="noopener">How to embed an image in a discussion</a></li>
                </ul>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Submission Instructions and Due Dates</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>Click "<em>Reply</em>" to post your question.</p>
                <p><strong>First response due:</strong> <em>${first_due}</em></p>
                <p><strong>Responses to peers due:</strong> <em>${peer_due}</em></p>
            </div>
        </div>
        <div style="background-color: ${primary}; color: white; padding: 5px; margin-top: 5px; border-radius: 5px; text-align: center;">
            <p style="text-align: right;"><em>Click on the Next button below to continue.</em> <span aria-hidden="true">▼</span></p>
        </div>
    </div>
</div>''')

    # Template 8: Quiz Instructions
    QUIZ = Template('''<div style="padding: 20px; border-radius: 5px;">
    <div style="padding: 20px; border-radius: 5px;">
        <h2 style="background-color: ${primary}; color: white; border-radius: 5px; padding: 16px;"><strong>Quiz Overview</strong></h2>
        <div style="border-color: #FFFFFF; padding-left: 15px; padding-right: 15px;">
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Goal and Purpose</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>${goal_purpose}</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Directions</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <ul>
                    <li>You have ${attempts} attempts to take the quiz.</li>
                    <li>This quiz is ${timed_status}.</li>
                </ul>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Grading</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>If for any reason you feel that your score is wrong, please send me a message through the Canvas INBOX.</p>
            </div>
            <h3 style="color: ${primary}; border-bottom: 2px solid ${secondary}; padding-bottom: 5px;"><strong>Technical Support</strong></h3>
            <div style="border-left: 5px solid ${secondary}; padding: 20px; margin-bottom: 25px; border-radius: 5px;">
                <p>Need help using Canvas Quizzes? If so, please review the following Canvas resources:</p>
                <ul>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-take-a-quiz-in-New-Quizzes/ta-p/291" target="_blank" rel="noopener">How do I take a quiz in New Quizzes?</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-take-a-quiz-where-I-can-only-view-one-question-at-a/ta-p/292" target="_blank" rel="noopener">How do I take a quiz where I can only view one question at a time in New Quizzes?</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-answer-each-type-of-question-in-New-Quizzes/ta-p/290" target="_blank" rel="noopener">How do I answer each type of question in New Quizzes?</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-submit-a-quiz/ta-p/475" target="_blank" rel="noopener">How do I submit a quiz?</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-view-my-quiz-results-as-a-student-in-New-Quizzes/ta-p/289" target="_blank" rel="noopener">How do I view my quiz results as a student in New Quizzes?</a></li>
                    <li><a href="https://community.canvaslms.com/t5/Student-Guide/How-do-I-know-if-I-can-retake-a-quiz-in-New-Quizzes/ta-p/287" target="_blank" rel="noopener">How do I know if I can retake a quiz in New Quizzes?</a></li>
                </ul>
            </div>
        </div>
    </div>
</div>''')

    # ── Canvas Component Templates ──────────────────────────────────

    TABS_CONTAINER = Template('''<div>
    <ul>
${tab_links}
    </ul>
${tab_panels}
</div>''')

    EXPANDER = Template('''<details style="margin-bottom: 16px;">
    <summary style="background-color: ${primary}; color: white; padding: 12px 16px; border-radius: 5px; cursor: pointer; font-weight: bold;">${title}</summary>
    <div style="padding: 16px; border: 1px solid #e0e0e0; border-top: none; border-radius: 0 0 5px 5px;">
${content}
    </div>
</details>''')

    CALLOUT_BOX = Template('''<div style="border-left: 4px solid ${primary}; padding: 16px; background: #f8f9fa; margin: 16px 0; border-radius: 4px;">
    <p style="margin: 0;"><strong>${title}</strong></p>
    <div style="margin-top: 8px;">${content}</div>
</div>''')

    BANNER = Template('''<div style="background-color: ${primary}; color: white; padding: 16px; border-radius: 5px; margin-bottom: 16px;">
    <h2 style="color: white; margin: 0;"><strong>${title}</strong></h2>
</div>''')

    ALTERNATING_BLOCK_WHITE = Template('''<div style="padding: 20px; margin-bottom: 4px; background-color: #ffffff; border-radius: 5px;">
${content}
</div>''')

    ALTERNATING_BLOCK_GRAY = Template('''<div style="padding: 20px; margin-bottom: 4px; background-color: #f5f5f5; border-radius: 5px;">
${content}
</div>''')

    # Template mapping
    TEMPLATES: dict[TemplateType, Template] = {
        TemplateType.GENERAL_CONTENT: GENERAL_CONTENT,
        TemplateType.FRONT_PAGE: FRONT_PAGE,
        TemplateType.ANNOUNCEMENT: ANNOUNCEMENT,
        TemplateType.MEET_INSTRUCTOR: MEET_INSTRUCTOR,
        TemplateType.MODULE_OVERVIEW: MODULE_OVERVIEW,
        TemplateType.ASSIGNMENT: ASSIGNMENT,
        TemplateType.DISCUSSION: DISCUSSION,
        TemplateType.QUIZ: QUIZ,
    }

    @classmethod
    def get_template(cls, template_type: TemplateType) -> Template:
        """Get a template by type.

        Args:
            template_type: Type of template to retrieve.

        Returns:
            Template object for the requested type.
        """
        return cls.TEMPLATES.get(template_type, cls.GENERAL_CONTENT)

    @classmethod
    def render(
        cls,
        template_type: TemplateType,
        primary: str,
        secondary: str,
        **kwargs,
    ) -> str:
        """Render a template with color scheme and content.

        Args:
            template_type: Type of template to render.
            primary: Primary color hex value.
            secondary: Secondary color hex value.
            **kwargs: Template-specific variables.

        Returns:
            Rendered HTML string.
        """
        template = cls.get_template(template_type)
        return template.safe_substitute(
            primary=primary,
            secondary=secondary,
            **kwargs,
        )

    @classmethod
    def render_component(
        cls,
        component_name: str,
        primary: str,
        secondary: str,
        **kwargs,
    ) -> str:
        """Render a component template by name.

        Args:
            component_name: Name of the component template (e.g., 'CALLOUT_BOX').
            primary: Primary color hex value.
            secondary: Secondary color hex value.
            **kwargs: Component-specific variables.

        Returns:
            Rendered HTML string.
        """
        template = getattr(cls, component_name, None)
        if template is None:
            raise ValueError(f"Unknown component template: {component_name}")
        return template.safe_substitute(
            primary=primary,
            secondary=secondary,
            **kwargs,
        )
